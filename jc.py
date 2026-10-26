#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧查查 / DataEye ADX行业版 数据采集器
=====================================

协议来源：adxray.dataeye.com 前端 bundle 逆向（app.js）
  host      : https://adxray.dataeye.com
  接口风格  : POST application/json, 业务参数平铺在 body
  公共参数  : thisTimes = int(now_ms / 100)
  签名      : sign = MD5( "k1=v1&k2=v2...(key 升序, 值 trim)" + "&key=g:%w0k7&q1v9^tRnLz!M" ).upper()
  身份      : body.token = userKey  (同时建议带浏览器 Cookie: SESSION/...)
  登录      : POST /user/login  (form: accountId, password, vCode)

用法：
  1) 浏览器登录 https://adxray.dataeye.com
  2) F12 -> Console 执行:
        copy(JSON.stringify({userKey: window.App && App.userKey, cookie: document.cookie}))
     或 Network 面板任选 /playlet/* 请求，Payload 里复制 token、Headers 里复制 Cookie
  3) 写入 config.json:  {"userKey": "...", "cookie": "SESSION=..."}
  4) python3 jc.py probe                  # 探活 + 打印各接口可用性
     python3 jc.py dates                  # 榜单可用日期
     python3 jc.py hot --date 2026-10-25  # 热度榜
     python3 jc.py search --kw 总裁        # 短剧搜索
     python3 jc.py export  --table hot    # 导出 CSV / XLSX
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
import warnings
from typing import Any, Dict, Iterable, List, Optional

warnings.filterwarnings("ignore")

try:
    import requests
except ImportError:
    sys.exit("缺少 requests: pip3 install --user requests")

HOST = "https://adxray.dataeye.com"
SIGN_SALT = "&key=g:%w0k7&q1v9^tRnLz!M"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 从 bundle 提取的 playlet（剧查查）接口族
ENDPOINTS = {
    # 榜单/概览
    "hot_ranking_date": "/playlet/getHotRankingDate",
    "list_hot_ranking": "/playlet/listHotRanking",
    "list_new_ranking": "/playlet/listNewRanking",
    "list_ranking": "/playlet/listRanking",
    "overview_trend": "/playlet/overview/getTrendList",
    "overview_type_top": "/playlet/overview/getPlayletTypeTop",
    "overview_tag_top": "/playlet/overview/getPlayletTagTop",
    "overview_publisher_top": "/playlet/overview/getPlayletPublisherTop",
    "overview_mobile_top": "/playlet/overview/getPlayletMobileTypeTop",
    # 搜索
    "search_playlet": "/playlet/search/searchPlaylet",
    "search_publisher": "/playlet/search/searchPublisher",
    "search_hot_title": "/playlet/searchHotTitle",
    "search_quick": "/search/quickSearch",
    # 详情/趋势
    "playlet_info": "/playlet/getPlayletInfo",
    "trend_by_playlet": "/playlet/listTrendByPlaylet",
    "trend_by_material": "/playlet/company/listTrendByMaterial",
    "all_labels": "/playlet/getAllLabels",
    # 发行/公司维度
    "publisher_ranking": "/playlet/listPublisherRanking",
    "publisher_playlet_ranking": "/playlet/listPublisherPlayletRanking",
    "publisher_distribution": "/playlet/listPublisherDistribution",
    "product_distribution": "/playlet/listProductDistribution",
    "company_info": "/playlet/company/getCompanyInfo",
    "media_distribution": "/playlet/company/listMediaDistribution",
}


def sign_params(params: Dict[str, Any]) -> str:
    """与前端 computeSignByParams 等价。数组转逗号串，字符串 trim，None -> 空。"""
    flat: Dict[str, str] = {}
    for k, v in params.items():
        if v is None or k in ("", None):
            continue
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        v = v.strip() if isinstance(v, str) else str(v)
        flat[str(k)] = v
    raw = "&".join(f"{k}={flat[k]}" for k in sorted(flat))
    return hashlib.md5((raw + SIGN_SALT).encode("utf-8")).hexdigest().upper()


class JuChacha:
    def __init__(self, user_key: str = "", cookie: str = "",
                 host: str = HOST, rate: float = 1.5, timeout: int = 25,
                 db: str = "data/juchacha.db"):
        self.user_key = (user_key or "").strip()
        self.host = host.rstrip("/")
        self.rate = max(0.0, rate)
        self.timeout = timeout
        self.db = db
        os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.host,
            "Referer": self.host + "/",
        })
        cookies = {}
        for seg in (cookie or "").split(";"):
            if "=" in seg:
                k, v = seg.split("=", 1)
                cookies[k.strip()] = v.strip()
        if cookies:
            self.s.cookies.update(cookies)
        self._last = 0.0

    # ---------- 底层 ----------
    def _throttle(self):
        dt = time.time() - self._last
        if dt < self.rate:
            time.sleep(self.rate - dt + random.uniform(0, 0.25))
        self._last = time.time()

    def request(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body = dict(payload or {})
        body["thisTimes"] = int(time.time() * 1000 / 100)
        body["sign"] = sign_params(body)          # sign 只覆盖业务参数 + thisTimes
        if self.user_key:
            body["token"] = self.user_key
        self._throttle()
        url = self.host + path
        r = self.s.post(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                        timeout=self.timeout)
        try:
            js = r.json()
        except Exception:
            raise RuntimeError(f"{path} 非 JSON 响应: HTTP {r.status_code} {r.text[:200]}")
        code = js.get("statusCode") or js.get("code")
        if code == 401 or str(js.get("msg", "")).lower() == "unauthorized":
            raise PermissionError(
                f"401 Unauthorized @ {path}\n"
                "  -> 需要登录态：config.json 的 userKey / cookie 未填或已过期。\n"
                "  -> 浏览器登录 adxray.dataeye.com，F12 Console:\n"
                "     copy(JSON.stringify({userKey: App.userKey, cookie: document.cookie}))"
            )
        if code not in (200, 0, None) and "data" not in js:
            raise RuntimeError(f"{path} 业务错误: {json.dumps(js, ensure_ascii=False)[:300]}")
        return js

    # ---------- 业务 ----------
    def hot_ranking_dates(self) -> Any:
        return self.request(ENDPOINTS["hot_ranking_date"], {}).get("data")

    def hot_ranking(self, date: str = "", page: int = 1, size: int = 20,
                    page_key: str = "pageIndex", size_key: str = "pageSize", **filters) -> Dict[str, Any]:
        p: Dict[str, Any] = {page_key: page, size_key: size}
        if date:
            p["date"] = date
            p["startDate"] = date
        p.update({k: v for k, v in filters.items() if v not in (None, "")})
        return self.request(ENDPOINTS["list_hot_ranking"], p)

    def search_playlet(self, keyword: str, page: int = 1, size: int = 20,
                       page_key: str = "pageIndex", size_key: str = "pageSize") -> Dict[str, Any]:
        return self.request(ENDPOINTS["search_playlet"],
                            {page_key: page, size_key: size, "keyword": keyword, "searchKey": keyword})

    def playlet_info(self, playlet_id: Any) -> Dict[str, Any]:
        return self.request(ENDPOINTS["playlet_info"], {"playletId": playlet_id, "id": playlet_id})

    # ---------- 落库 ----------
    def save(self, table: str, rows: Iterable[Dict[str, Any]], raw: bool = False) -> int:
        rows = list(rows)
        if not rows:
            return 0
        con = sqlite3.connect(self.db)
        try:
            if raw:
                con.execute(f"CREATE TABLE IF NOT EXISTS {table} "
                            "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, path TEXT, payload TEXT)")
                con.executemany(f"INSERT INTO {table}(ts,path,payload) VALUES(?,?,?)",
                                [(time.strftime("%Y-%m-%d %H:%M:%S"), table, json.dumps(r, ensure_ascii=False))
                                 for r in rows])
            else:
                cols: List[str] = []
                for r in rows:
                    for k in r:
                        if k not in cols:
                            cols.append(k)
                con.execute(f"CREATE TABLE IF NOT EXISTS {table} ("
                            + ", ".join(f'"{c}"' for c in cols) + ", _ingested_at TEXT)")
                sql = (f'INSERT INTO {table} (' + ", ".join(f'"{c}"' for c in cols) + ", _ingested_at) VALUES ("
                       + ",".join("?" * (len(cols) + 1)) + ")")
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                con.executemany(sql, [[r.get(c) if not isinstance(r.get(c), (dict, list))
                                       else json.dumps(r.get(c), ensure_ascii=False)
                                       for c in cols] + [now] for r in rows])
            con.commit()
        finally:
            con.close()
        return len(rows)


def load_config(path: str = "config.json") -> Dict[str, Any]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def make_client(cfg: Dict[str, Any], args) -> JuChacha:
    user_key = getattr(args, "token", None) or os.environ.get("JC_TOKEN") or cfg.get("userKey", "")
    cookie = getattr(args, "cookie", None) or os.environ.get("JC_COOKIE") or cfg.get("cookie", "")
    return JuChacha(user_key=user_key, cookie=cookie, rate=getattr(args, "rate", 1.5),
                    db=os.path.join(cfg.get("outDir", "data"), "juchacha.db"))


# ---------------- CLI ----------------
def cmd_probe(c: JuChacha, args):
    ok = bad = auth = 0
    for name, path in ENDPOINTS.items():
        try:
            js = c.request(path, {})
            n = len(js.get("data") or []) if isinstance(js.get("data"), list) else "-"
            print(f"[200] {name:26s} {path:45s} data={n}")
            ok += 1
        except PermissionError:
            print(f"[401] {name:26s} {path:45s} 需登录")
            auth += 1
        except Exception as e:
            print(f"[ERR] {name:26s} {path:45s} {str(e)[:90]}")
            bad += 1
    print(f"\n可用 {ok} / 需登录 {auth} / 失败 {bad}")
    if auth and not c.user_key:
        print("提示：先配置 userKey（见 README.md 第一步）")


def cmd_dates(c: JuChacha, args):
    print(json.dumps(c.hot_ranking_dates(), ensure_ascii=False, indent=2))


def _dig_rows(js: Dict[str, Any]) -> List[Dict[str, Any]]:
    d = js.get("data")
    if isinstance(d, list):
        return [r for r in d if isinstance(r, dict)]
    if isinstance(d, dict):
        for k in ("list", "records", "rows", "dataList", "items", "result"):
            if isinstance(d.get(k), list):
                return [r for r in d[k] if isinstance(r, dict)]
        return [d]
    return []


def cmd_hot(c: JuChacha, args):
    rows, page = [], 1
    while page <= args.pages:
        js = c.hot_ranking(date=args.date, page=page, size=args.size,
                           page_key=args.page_key, size_key=args.size_key)
        got = _dig_rows(js)
        if not got:
            break
        rows.extend(got)
        print(f"page {page}: +{len(got)} (total {len(rows)})")
        d = js.get("data")
        total = d.get("total") if isinstance(d, dict) else None
        if total and len(rows) >= int(total):
            break
        page += 1
    if args.raw:
        c.save("hot_ranking_raw", rows, raw=True)
    n = c.save("hot_ranking", rows)
    print(f"入库 {n} 行 -> {c.db} (table: hot_ranking)")
    out = os.path.join(os.path.dirname(c.db), f"hot_ranking_{args.date or 'latest'}.csv")
    if rows:
        write_csv(out, rows)
        print("CSV:", out)


def cmd_search(c: JuChacha, args):
    rows, page = [], 1
    while page <= args.pages:
        js = c.search_playlet(args.kw, page=page, size=args.size)
        got = _dig_rows(js)
        if not got:
            break
        rows.extend(got)
        page += 1
    c.save("search_" + "".join(ch for ch in args.kw if ch.isalnum())[:20] or "search", rows)
    out = os.path.join(os.path.dirname(c.db), f"search_{args.kw}.csv")
    write_csv(out, rows)
    print(f"{len(rows)} 行 -> {out}")


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
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in r.items() if k in cols})


def cmd_export(c: JuChacha, args):
    con = sqlite3.connect(c.db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"SELECT * FROM {args.table}")]
    con.close()
    base = os.path.join(os.path.dirname(c.db), args.table)
    write_csv(base + ".csv", rows)
    print(f"CSV -> {base}.csv ({len(rows)} 行)")
    try:
        from openpyxl import Workbook
    except ImportError:
        print("（可选）pip3 install --user openpyxl 后可同时导出 XLSX")
        return
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(args.table[:30])
    if rows:
        cols = list(rows[0].keys())
        ws.append(cols)
        for r in rows:
            ws.append([r.get(k) for k in cols])
    wb.save(base + ".xlsx")
    print(f"XLSX -> {base}.xlsx")


def main():
    ap = argparse.ArgumentParser(description="剧查查 / DataEye ADX 采集器")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--token", help="userKey，覆盖 config")
    ap.add_argument("--cookie", help="浏览器 Cookie 串，覆盖 config")
    ap.add_argument("--rate", type=float, default=1.5, help="请求间隔秒（默认 1.5）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="探活全部接口")

    sub.add_parser("dates", help="榜单可用日期")

    p = sub.add_parser("hot", help="采热度榜")
    p.add_argument("--date", default="")
    p.add_argument("--pages", type=int, default=5)
    p.add_argument("--size", type=int, default=20)
    p.add_argument("--page-key", default="pageIndex")
    p.add_argument("--size-key", default="pageSize")
    p.add_argument("--raw", action="store_true")

    p = sub.add_parser("search", help="按关键词搜短剧")
    p.add_argument("--kw", required=True)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--size", type=int, default=20)

    p = sub.add_parser("export", help="表导出 CSV/XLSX")
    p.add_argument("--table", default="hot_ranking")

    args = ap.parse_args()
    cfg = load_config(args.config)
    c = make_client(cfg, args)
    try:
        {"probe": cmd_probe, "dates": cmd_dates, "hot": cmd_hot,
         "search": cmd_search, "export": cmd_export}[args.cmd](c, args)
    except PermissionError as e:
        sys.exit(f"\n[401] {e}")
    except RuntimeError as e:
        sys.exit(f"\n[ERR] {e}")
    except KeyboardInterrupt:
        sys.exit("\n已中断，已抓到的数据已落库。")


if __name__ == "__main__":
    main()
