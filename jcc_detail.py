#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧查查小程序 · 三榜「详情级」采集
================================
在 jcc_rank.py 基础上补齐两类东西：

1. 全字段展平 —— 榜单原始 JSON 里的 playletUser / producerList / contractorList /
   platformList / playletTags / contentTypes 全部拆成可读列，一部剧一行。
2. 上榜历史（免登录可用）—— 逐剧打 /playlet/rankHonor，拿到该剧在各榜的历史排名、
   连续霸榜天数、日期序列。

3. 剧集详情（需小程序登录态）—— 有 token 时调用：
      /playlet/getPlayletInfoV2     基础详情（简介/题材/集数/上线/平台/出品…）
      /playlet/topData              top 数据
      /playlet/getPlayletRankData   6 类统计
      /playlet/audienceAnalysis     受众分析
      /playlet/makingTeam           制作团队
      /playlet/listTrendByPlaylet   素材投放趋势
      /playlet/listProductDistribution      投放剧场分布
      /playlet/listPublisherDistribution    投放公司分布
   无 token 时这些接口对未登录返回 {"statusCode":200,"content":null}，
   脚本自动跳过，不报错。

token 获取（任一）：
  A. 真机抓包：mitmproxy 起代理，微信里打开剧查查小程序，翻一次榜单，
     在请求头/Cookie 里复制 token（字段名可能是 token / Authorization / SESSION）。
  B. 微信开发者工具：导入小程序源码，Network 面板看首个 /login/loginUser 之后的请求头。
  C. 有 cookie 也行：--cookie "SESSION=…; …"

用法：
  python3 jcc_detail.py                        # 三榜 + 上榜历史
  python3 jcc_detail.py --no-honor             # 只要榜单宽表（快）
  python3 jcc_detail.py --token XXX            # 追加剧集详情
  python3 jcc_detail.py --day 2026-10-25 --honor-workers 4
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")
import requests  # noqa: E402

from jcc_rank import RANKS, get as jget, latest_day  # noqa: E402

BASE = "https://playlet-applet.dataeye.com"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 MicroMessenger/8.0.50")

DETAIL_EPS: List[Tuple[str, str, Dict[str, Any]]] = [
    ("base_info", "/playlet/getPlayletInfoV2", {}),
    ("top_data", "/playlet/topData", {}),
    ("rank_data", "/playlet/getPlayletRankData", {}),
    ("audience", "/playlet/audienceAnalysis", {}),
    ("team", "/playlet/makingTeam", {}),
    ("trend", "/playlet/listTrendByPlaylet", {"pageId": 1, "pageSize": 60}),
    ("dist_product", "/playlet/listProductDistribution", {"pageId": 1, "pageSize": 60}),
    ("dist_publisher", "/playlet/listPublisherDistribution", {"pageId": 1, "pageSize": 60}),
]

# ---------------- 标签字典（/playlet/playletFilterList） ----------------
TAG_DIM: Dict[int, Tuple[str, str]] = {}   # tagId -> (维度, 标签)
_TAG_LOADED = False


def load_tag_dict() -> Dict[int, Tuple[str, str]]:
    """把 tags 里的数字 id 翻成中文标签。维度：时代背景/主题情节/角色设定/短剧类型/频道。"""
    global _TAG_LOADED
    if _TAG_LOADED:
        return TAG_DIM
    try:
        js = jget("/playlet/playletFilterList", {"isSearch": 0})
        for dim in js.get("content") or []:
            title = dim.get("title") or ""
            for it in dim.get("content") or []:
                v = it.get("value")
                label = it.get("label")
                if isinstance(v, int) and label:
                    TAG_DIM.setdefault(v, (title, label))
        _TAG_LOADED = True
        print(f"   标签字典: {len(TAG_DIM)} 个")
    except Exception as e:
        print("   标签字典加载失败:", e)
    return TAG_DIM


def translate_tags(tags_field: Any) -> Tuple[str, str]:
    """返回 (逗号分隔中文标签, 维度分组串)"""
    ids: List[int] = []
    if isinstance(tags_field, list):
        ids = [int(x) for x in tags_field if str(x).isdigit()]
    elif isinstance(tags_field, str):
        raw = tags_field.strip()
        if raw.startswith("["):
            try:
                ids = [int(x) for x in json.loads(raw) if str(x).isdigit()]
            except Exception:
                ids = []
        else:
            ids = [int(x) for x in raw.replace("、", ",").split(",") if x.strip().isdigit()]
    labels, groups = [], {}
    for i in ids:
        d = TAG_DIM.get(i)
        if not d:
            continue
        dim, lab = d
        labels.append(lab)
        groups.setdefault(dim, []).append(lab)
    return "、".join(labels), "；".join(f"{k}={'/'.join(v)}" for k, v in groups.items())


# 榜单荣誉 type 编码（来自 /playlet/rankHonor 与 rankConfig 口径）
HONOR_TYPE = {1: "热力榜", 2: "抖音热播", 3: "快手热播", 4: "漫剧热播",
              5: "动态漫榜", 6: "沙雕漫榜", 7: "真人AI榜"}

_lock = threading.Lock()
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Origin": "https://jcc.dataeye.com",
                       "Referer": "https://jcc.dataeye.com/"})


# ---------------- 工具：展平 ----------------
def _join_names(items: Any) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        return items
    out = []
    for it in items:
        if isinstance(it, dict):
            nm = (it.get("name") or it.get("publisherName") or it.get("companyName")
                  or it.get("nickname") or "")
            if nm:
                out.append(str(nm))
        else:
            out.append(str(it))
    return "、".join(x for x in out if x)


def _user_str(u: Any) -> str:
    if not isinstance(u, dict):
        return str(u) if u else ""
    parts = [u.get("nickname") or "", u.get("account") or "", u.get("userId") or ""]
    parts = [str(p) for p in parts if p]
    return " / ".join(parts)


def flatten_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in r.items():
        if k.startswith("_"):
            out[k] = v
            continue
        if isinstance(v, list):
            if v and isinstance(v[0], dict):
                out[k] = _join_names(v)
                # 额外把 dict 里的 id 拿出来
                ids = [str(x.get("id") or x.get("publisherId") or "") for x in v if isinstance(x, dict)]
                ids = [i for i in ids if i]
                if ids:
                    out[k + "_ids"] = "、".join(ids)
            else:
                out[k] = "、".join(str(x) for x in v)
        elif isinstance(v, dict):
            out[k] = _user_str(v)
        elif isinstance(v, str) and v[:1] in "[{" and v[-1:] in "]}":
            try:
                parsed = json.loads(v)
                out[k] = _join_names(parsed) if isinstance(parsed, list) else _user_str(parsed)
            except Exception:
                out[k] = v
        else:
            out[k] = v
    # tags 数字 id -> 中文标签（维度分组）
    src = r.get("tags") if r.get("tags") is not None else r.get("playletTags")
    names, dims = translate_tags(src)
    if names:
        out["tags_中文"] = names
        out["tags_维度"] = dims
    return out


def dig(js: Any, *keys: str) -> Any:
    cur = js
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ---------------- 抓榜单 ----------------
def fetch_rank_rows(name: str, day: str, pages: int = 6, size: int = 30) -> List[Dict[str, Any]]:
    path, fixed = RANKS[name]
    rows, seen, total = [], set(), None
    for page in range(1, pages + 1):
        js = jget(path, {**fixed, "day": day, "pageId": page, "pageSize": size})
        if (js.get("page") or {}).get("totalRecords") is not None:
            total = js["page"]["totalRecords"]
        chunk = js.get("content") or []
        if not chunk:
            break
        new = 0
        for r in chunk:
            pid = r.get("playletId")
            if pid in seen:
                continue
            seen.add(pid)
            r["_rank_name"] = name
            r["_rank_day"] = r.get("rankingDate") or r.get("calculateDate") or day
            r["_rank_pos"] = r.get("ranking")
            rows.append(r)
            new += 1
        if new == 0:
            break
        if total and len(rows) >= total:
            break
        time.sleep(0.5)
    return rows


# ---------------- 上榜历史 ----------------
def fetch_honor(playlet_id: int, cookies: Optional[str] = None) -> Dict[str, Any]:
    try:
        js = jget("/playlet/rankHonor", {"playletId": playlet_id, "pageId": 1, "pageSize": 60})
        blocks = js.get("content") or []
        hist = []
        for b in blocks:
            d = b.get("dimDate")
            for e in b.get("list") or []:
                hist.append({"dimDate": e.get("dimDate") or d,
                             "rankType": e.get("type"), "rankNum": e.get("rankNum")})
        hist.sort(key=lambda x: (x["dimDate"] or "", x["rankType"] or 0))
        by_type: Dict[int, List[Dict[str, Any]]] = {}
        for h in hist:
            by_type.setdefault(h["rankType"], []).append(h)
        best = {t: min(v, key=lambda x: x["rankNum"] or 10 ** 6) for t, v in by_type.items()}
        return {
            "playletId": playlet_id,
            "honor_days": len({h["dimDate"] for h in hist}),
            "honor_ranks": "；".join(
                f"{HONOR_TYPE.get(t, 'type' + str(t))}:第{best[t]['rankNum']}名({best[t]['dimDate']})"
                for t in sorted(best)),
            "honor_history": "；".join(
                f"{h['dimDate']} type{h['rankType']} 第{h['rankNum']}名" for h in hist),
            "honor_types": ",".join(str(t) for t in sorted(best)),
        }
    except Exception as e:
        return {"playletId": playlet_id, "honor_error": str(e)[:120]}


# ---------------- 剧集详情 ----------------
def fetch_detail(playlet_id: int, token: str, cookie: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"playletId": playlet_id}
    hdrs = {"User-Agent": UA, "Origin": "https://jcc.dataeye.com"}
    if token:
        hdrs["token"] = token
        hdrs["Authorization"] = token
    if cookie:
        hdrs["Cookie"] = cookie
    got_any = False
    for label, path, extra in DETAIL_EPS:
        try:
            r = SESSION.get(BASE + path, params={"playletId": playlet_id, **extra},
                            headers=hdrs, timeout=25)
            js = r.json() or {}
            c = js.get("content")
            if js.get("statusCode") == 401:
                out[label] = "__need_login__"
                continue
            if c in (None, [], {}):
                continue
            got_any = True
            if label == "base_info" and isinstance(c, dict):
                f = flatten_row(c)
                for k, v in f.items():
                    out[f"info_{k}"] = v
            elif isinstance(c, list):
                out[label] = json.dumps(c[:30], ensure_ascii=False)[:4000]
                out[label + "_n"] = len(c)
            else:
                out[label] = json.dumps(c, ensure_ascii=False)[:4000]
            time.sleep(0.3)
        except Exception as e:
            out[label + "_err"] = str(e)[:80]
    out["detail_ok"] = 1 if got_any else 0
    return out


# ---------------- 落盘 ----------------
def write_csv(path: str, rows: List[Dict[str, Any]]):
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})


def write_xlsx(path: str, sheets: Dict[str, List[Dict[str, Any]]]):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("（可选）pip3 install openpyxl 才能导出 XLSX")
        return ""
    wb = Workbook()
    wb.remove(wb.active)
    hdr_fill = PatternFill("solid", fgColor="FC563D")
    for name, rows in sheets.items():
        if not rows:
            continue
        ws = wb.create_sheet(name[:28])
        cols: List[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        # 常用列前置，便于阅读
        front = [c for c in ("_rank_pos", "ranking", "playletName", "hotValueText", "hotValue",
                             "playCount", "playCountAdd", "totalEpisode", "playletTags",
                             "producerCompany", "producerList", "contractorCompany",
                             "contractorList", "platformList", "playletUser", "manufacturer",
                             "listDays", "tags_中文", "tags_维度", "rankingDate", "calculateDate", "releaseStartDate",
                             "playletNativeId", "playletId") if c in cols]
        cols = front + [c for c in cols if c not in front]
        ws.append(cols)
        for i, c in enumerate(ws[1], 1):
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = hdr_fill
            c.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append([r.get(k) for k in cols])
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        for i, c in enumerate(cols, 1):
            width = min(46, max(9, len(str(c)) * 1.4))
            ws.column_dimensions[get_column_letter(i)].width = width
    wb.save(path)
    return path


def save_db(db: str, tables: Dict[str, List[Dict[str, Any]]]):
    con = sqlite3.connect(db)
    for name, rows in tables.items():
        if not rows:
            continue
        cols: List[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
        con.execute(f'CREATE TABLE "{name}" (' + ", ".join(f'"{c}"' for c in cols) + ")")
        con.executemany(f'INSERT INTO "{name}" VALUES (' + ",".join("?" * len(cols)) + ")",
                        [[r.get(c) for c in cols] for r in rows])
    con.commit()
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="")
    ap.add_argument("--token", default=os.environ.get("JCC_TOKEN", ""))
    ap.add_argument("--cookie", default=os.environ.get("JCC_COOKIE", ""))
    ap.add_argument("--out", default="data/jcc")
    ap.add_argument("--no-honor", action="store_true", help="跳过上榜历史（快）")
    ap.add_argument("--no-detail", action="store_true", help="跳过剧集详情")
    ap.add_argument("--honor-workers", type=int, default=4)
    a = ap.parse_args()

    days = {n: a.day or latest_day(n) for n in RANKS}
    print("日期:", days)
    load_tag_dict()

    raw_rows: List[Dict[str, Any]] = []
    for name in RANKS:
        if not days[name]:
            continue
        print(f">> {name} ({days[name]})")
        raw_rows.extend(fetch_rank_rows(name, days[name]))

    if not raw_rows:
        sys.exit("没抓到榜单数据")

    day = max(days.values())
    os.makedirs(a.out, exist_ok=True)
    uniq_ids = sorted({r["playletId"] for r in raw_rows if r.get("playletId")})
    print(f"共 {len(raw_rows)} 行 / {len(uniq_ids)} 部不重复剧")

    # 展平宽表（按榜分表）
    wide = [flatten_row(r) for r in raw_rows]

    # 上榜历史
    honors: Dict[int, Dict[str, Any]] = {}
    if not a.no_honor:
        print(f">> 上榜历史：{len(uniq_ids)} 部，{a.honor_workers} 并发")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=a.honor_workers) as ex:
            futs = {ex.submit(fetch_honor, pid): pid for pid in uniq_ids}
            for i, f in enumerate(as_completed(futs), 1):
                r = f.result()
                honors[r["playletId"]] = r
                if i % 20 == 0:
                    print(f"   {i}/{len(uniq_ids)} …")
                time.sleep(0.15)
        print(f"   完成，用时 {time.time()-t0:.0f}s")

    # 详情
    details: Dict[int, Dict[str, Any]] = {}
    if not a.no_detail:
        print(f">> 剧集详情：token={'有' if a.token else '无'}")
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(fetch_detail, pid, a.token, a.cookie): pid for pid in uniq_ids}
            for i, f in enumerate(as_completed(futs), 1):
                r = f.result()
                details[r["playletId"]] = r
                if i % 20 == 0:
                    print(f"   {i}/{len(uniq_ids)} …")
        ok = sum(1 for d in details.values() if d.get("detail_ok"))
        print(f"   详情可用 {ok}/{len(uniq_ids)}"
              + ("" if ok else "（接口对未登录返回 null，需 --token）"))

    # 合并：宽表 + 荣誉 + 详情
    merged: List[Dict[str, Any]] = []
    for w in wide:
        pid = w.get("playletId")
        row = dict(w)
        h = honors.get(pid)
        if h:
            row.update({k: v for k, v in h.items() if k != "playletId"})
        d = details.get(pid)
        if d:
            row.update({k: v for k, v in d.items() if k != "playletId"})
        merged.append(row)

    # 输出
    sheets: Dict[str, List[Dict[str, Any]]] = {}
    for name in RANKS:
        rs = [m for m in merged if m.get("_rank_name") == name]
        if rs:
            sheets[name] = rs
    sheets["全部明细"] = merged
    if honors:
        sheets["上榜历史"] = list(honors.values())
    if details and any(d.get("detail_ok") for d in details.values()):
        shrunk = [{k: (v if not isinstance(v, str) or len(v) < 900 else v[:900])
                   for k, v in d.items() if not k.endswith("_err")} for d in details.values()]
        sheets["剧集详情"] = shrunk

    csvp = os.path.join(a.out, f"三榜详情_{day}.csv")
    write_csv(csvp, merged)
    xlsxp = write_xlsx(os.path.join(a.out, f"剧查查三榜详情_{day}.xlsx"), sheets)
    dbp = os.path.join(a.out, "jcc_ranks.db")
    save_db(dbp, {"rank_detail_" + day: merged, "honor": list(honors.values())}
            if honors else {"rank_detail_" + day: merged})

    print("\n=== 每榜 Top5（含关键详情字段）===")
    for name, rs in sheets.items():
        if name in ("全部明细", "上榜历史", "剧集详情"):
            continue
        rs = sorted(rs, key=lambda x: x.get("_rank_pos") or 10 ** 6)[:5]
        print(f"\n【{name}】")
        for r in rs:
            print(f"  {r.get('_rank_pos')}. {str(r.get('playletName'))[:24]:26s}"
                  f" 热度={str(r.get('hotValueText') or r.get('playCountAdd') or '-'):10s}"
                  f" 集数={str(r.get('totalEpisode') or '-'):>4s}"
                  f" 标签={str(r.get('playletTags') or r.get('tags') or '')[:22]:24s}"
                  f" 出品={str(r.get('producerList') or r.get('producerCompany') or '')[:16]:18s}"
                  f" 上榜={str(r.get('honor_days') or '-')}天")

    print(f"\nCSV  : {csvp}")
    if xlsxp:
        print(f"XLSX : {xlsxp}  （sheets: {', '.join(sheets)}）")
    print(f"SQLite: {dbp}")


if __name__ == "__main__":
    main()
