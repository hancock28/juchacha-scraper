#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剧查查三榜 → 红果站内详情补全（jcc_hongguo.py）
================================================

发现：剧查查榜单里的 `playletNativeId` **就是红果（hongguoduanju.com）的 series_id**，
而红果详情页是 SSR，`seriesDetail` 直接落在 HTML 里，**免登录、免签名**。

    https://hongguoduanju.com/detail?series_id=<playletNativeId>

可拿到：剧名、简介、题材 tags、总集数、可看集数、付费类型、上线时间、封面、
演员 celebrities、每集 vid（1000 集也有）、连载状态。

用法：
  python3 jcc_hongguo.py                 # 三榜最新日期 + 红果详情
  python3 jcc_hongguo.py --day 2026-10-25
  python3 jcc_hongguo.py --workers 8     # 并发（默认 6）
  python3 jcc_hongguo.py --limit 10      # 只跑前 10 部（调试）

说明：本机若开了代理/全局 DNS 劫持（域名被解析成 198.18.x fake-ip），
脚本会先用 DoH 拿真实 IP，再 patch socket 解析，绕过劫持。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import ssl
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")
import requests  # noqa: E402

from jcc_rank import RANKS, get as jget, latest_day  # noqa: E402
from jcc_detail import (fetch_rank_rows, flatten_row, load_tag_dict,  # noqa: E402
                        fetch_honor, write_csv, write_xlsx)

HG_HOST = "hongguoduanju.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_resolved: Dict[str, str] = {}
_resolve_lock = threading.Lock()


def doh_resolve(host: str = HG_HOST) -> Optional[str]:
    """用 DoH 拿真实 A 记录（绕开本机 fake-ip 劫持）。"""
    for url, qtype in (
        (f"https://223.5.5.5/resolve?name={host}&type=A", "ali"),
        (f"https://1.1.1.1/dns-query?name={host}&type=A", "cf"),
    ):
        try:
            r = requests.get(url, timeout=8,
                             headers={"accept": "application/dns-json", "User-Agent": UA})
            d = r.json()
            ans = d.get("Answer") or []
            ips = [a["data"] for a in ans if a.get("type") == 1]
            if ips:
                return ips[0]
        except Exception:
            continue
    return None


def patch_dns(host: str = HG_HOST) -> Optional[str]:
    """把 host 固定解析到 DoH 拿到的真实 IP，保留 SNI / Host 为原域名。"""
    ip = doh_resolve(host)
    if not ip:
        return None
    _resolved[host] = ip
    orig = socket.getaddrinfo

    def fake(hostname, port, family=0, type=0, proto=0, flags=0):
        if hostname == host:
            return orig(ip, port, family, type, proto, flags)
        return orig(hostname, port, family, type, proto, flags)

    socket.getaddrinfo = fake  # type: ignore[assignment]
    print(f"   DNS: {host} -> {ip} (DoH)")
    return ip


def extract_series(html: str) -> Optional[Dict[str, Any]]:
    """从红果详情页 HTML 里抠出 seriesDetail 这个 JSON 对象。"""
    key = '"seriesDetail":'
    i = html.find(key)
    if i < 0:
        return None
    s = html.index("{", i + len(key) - 1)
    depth, j, instr, esc = 0, s, False, False
    while j < len(html):
        c = html[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
        j += 1
    try:
        return json.loads(html[s:j])
    except Exception:
        return None


def fetch_hongguo(native_id: Any, retry: int = 2) -> Dict[str, Any]:
    """流式读取，读到 seriesDetail 即停；流被服务器截断时仍尝试用已收数据解析。"""
    sid = str(native_id or "").strip()
    if not sid.isdigit():
        return {"hg_ok": 0, "hg_err": "no_native_id"}
    url = f"https://{HG_HOST}/detail?series_id={sid}"
    last = ""
    for attempt in range(retry + 1):
        buf = ""
        try:
            with requests.get(url, timeout=45, stream=True, verify=False, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": f"https://{HG_HOST}/",
            }) as r:
                if r.status_code != 200:
                    last = f"HTTP {r.status_code}"
                    time.sleep(1.2 * (attempt + 1))
                    continue
                for chunk in r.iter_content(65536, decode_unicode=False):
                    buf += chunk.decode("utf-8", "ignore")
                    hit = buf.find('"seriesDetail":')
                    if hit >= 0 and buf.find('"vid_list"', hit) >= 0 and buf.rstrip().endswith(("}", ">", "script>")):
                        break
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:50]}"
            if '"seriesDetail":' not in buf:
                time.sleep(1.2 * (attempt + 1))
                continue
            # 流中断但已收到 seriesDetail，继续尝试解析
        d = extract_series(buf)
        if not d:
            last = f"parse failed (len={len(buf)})"
            time.sleep(1.2 * (attempt + 1))
            continue
        vids = d.get("vid_list") or []
        celebs = d.get("celebrities") or []
        info = d.get("series_episode_info") or {}
        return {
            "hg_ok": 1,
            "hg_series_id": d.get("series_id"),
            "hg_name": d.get("series_name"),
            "hg_intro": d.get("series_intro"),
            "hg_tags": "、".join(d.get("tags") or []),
            "hg_episode_cnt": info.get("episode_total_cnt") or d.get("episode_cnt"),
            "hg_accessible_episode": d.get("accessible_episode_cnt"),
            "hg_episode_text": d.get("episode_right_text"),
            "hg_status": "连载中" if info.get("series_status") == 1 else "已完结",
            "hg_pay_type": d.get("pay_type"),
            "hg_first_visible": d.get("first_visible_time"),
            "hg_cover": d.get("series_cover"),
            "hg_vid_count": len(vids),
            "hg_vid_head": "、".join(vids[:5]),
            "hg_celebrities": "、".join(
                str(c.get("name") or c.get("celebrity_name") or "")
                for c in celebs if isinstance(c, dict)),
            "hg_celebrity_n": len(celebs),
        }
    return {"hg_ok": 0, "hg_err": last}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="")
    ap.add_argument("--out", default="data/jcc")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 部（调试）")
    ap.add_argument("--no-honor", action="store_true")
    a = ap.parse_args()

    days = {n: a.day or latest_day(n) for n in RANKS}
    print("日期:", days)
    load_tag_dict()
    print(">> 红果详情页可达性检查")
    if not patch_dns(HG_HOST):
        print("   DoH 失败，直接尝试系统 DNS")
    probe = fetch_hongguo("7698294694886788121")
    if probe.get("hg_ok"):
        print(f"   连通：{probe.get('hg_name')} / {probe.get('hg_episode_text')}")
    else:
        print(f"   连接异常：{probe.get('hg_err')}（继续，逐剧会各自重试）")

    raw_rows: List[Dict[str, Any]] = []
    for name in RANKS:
        if not days[name]:
            continue
        print(f">> {name} ({days[name]})")
        raw_rows.extend(fetch_rank_rows(name, days[name]))
    if not raw_rows:
        sys.exit("没抓到榜单数据")

    day = max(days.values())
    uniq: Dict[int, str] = {}
    no_id = 0
    for r in raw_rows:
        pid = r.get("playletId")
        nid = r.get("playletNativeId")
        if pid and nid and pid not in uniq:
            uniq[pid] = str(nid)
        elif pid and not nid:
            no_id += 1
    items = list(uniq.items())
    if a.limit:
        items = items[:a.limit]
    print(f"共 {len(raw_rows)} 行 / {len(items)} 部可补红果详情"
          f"（另有 {no_id} 行属于抖音原生榜，DataEye 未给 playletNativeId，红果查不到）")

    # 红果详情
    print(f">> 红果详情抓取（{a.workers} 并发，每页约 800KB）")
    hg: Dict[int, Dict[str, Any]] = {}
    t0 = time.time()
    lock = threading.Lock()
    done = [0]

    def work(pid: int, nid: str):
        r = fetch_hongguo(nid)
        with lock:
            done[0] += 1
            if done[0] % 10 == 0:
                print(f"   {done[0]}/{len(items)} … ({time.time()-t0:.0f}s)")
        return pid, r

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, pid, nid) for pid, nid in items]
        for f in as_completed(futs):
            pid, r = f.result()
            hg[pid] = r
    ok = sum(1 for v in hg.values() if v.get("hg_ok"))
    print(f"   成功 {ok}/{len(items)}，用时 {time.time()-t0:.0f}s")

    # 上榜历史
    honors: Dict[int, Dict[str, Any]] = {}
    if not a.no_honor:
        print(">> 上榜历史")
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fetch_honor, pid): pid for pid in uniq}
            for f in as_completed(futs):
                r = f.result()
                honors[r["playletId"]] = r
        print(f"   {len(honors)} 部")

    # 合并
    merged: List[Dict[str, Any]] = []
    for r in raw_rows:
        w = flatten_row(r)
        pid = r.get("playletId")
        w.update({k: v for k, v in (hg.get(pid) or {}).items() if k != "playletId"})
        w.update({k: v for k, v in (honors.get(pid) or {}).items() if k != "playletId"})
        merged.append(w)

    os.makedirs(a.out, exist_ok=True)
    sheets: Dict[str, List[Dict[str, Any]]] = {}
    for name in RANKS:
        rs = [m for m in merged if m.get("_rank_name") == name]
        if rs:
            sheets[name] = rs
    sheets["全部明细"] = merged
    if honors:
        sheets["上榜历史"] = list(honors.values())
    hg_rows = [{"playletId": pid, **{k: v for k, v in r.items() if k != "playletId"}}
               for pid, r in hg.items()]
    if any(r.get("hg_ok") for r in hg_rows):
        sheets["红果详情"] = hg_rows

    csvp = os.path.join(a.out, f"三榜详情_红果_{day}.csv")
    write_csv(csvp, merged)
    xlsxp = write_xlsx(os.path.join(a.out, f"剧查查三榜_红果详情_{day}.xlsx"), sheets)

    # 摘要
    print("\n=== 补全效果（各榜 Top3）===")
    for name in RANKS:
        rs = sheets.get(name) or []
        rs = sorted(rs, key=lambda x: x.get("_rank_pos") or 10 ** 6)[:3]
        print(f"\n【{name}】")
        for m in rs:
            intro = str(m.get("hg_intro") or "")[:46]
            print(f"  {m.get('_rank_pos')}. {str(m.get('hg_name') or m.get('playletName'))[:22]:24s}"
                  f" {str(m.get('hg_episode_text') or ''):10s} {str(m.get('hg_status') or ''):4s}"
                  f" 演员{str(m.get('hg_celebrity_n') or 0):>2s} 集id{str(m.get('hg_vid_count') or 0):>4s}"
                  f" ｜{intro}")

    print(f"\nCSV : {csvp}")
    print(f"XLSX: {xlsxp}  (sheets: {', '.join(sheets)})")


if __name__ == "__main__":
    main()
