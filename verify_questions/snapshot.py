"""指令 1 盤點：把 MCP tool 的原始輸出存成 snapshots/<日期>.json，並與上一份比較。

用法（repo 根目錄）：
    .venv/bin/python verify_questions/snapshot.py            # 存今天的 snapshot 並印出比較
    .venv/bin/python verify_questions/snapshot.py --no-save  # 只印比較

呼叫的是 server.py 裡的同一組 tool 函式（crawl_pop_tables、pop_raw_quality、
crawl_status、http_stats），所以內容與透過 MCP 呼叫相同。
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SNAP_DIR = os.path.join(HERE, "snapshots")


def take() -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        import server

        return {
            "taken_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "crawl_pop_tables": server.crawl_pop_tables(),
            "pop_raw_quality": server.pop_raw_quality(),
            "crawl_status": server.crawl_status(lookback_minutes=1440),
            "http_stats": server.http_stats(minutes=1440),
        }


def _quality(snap: dict) -> dict:
    return {f'{r["raw_view"]}.{r["json_column"]}': r for r in snap.get("pop_raw_quality", [])}


def _rows(snap: dict) -> dict:
    return {r["table_name"]: r["rows"] for r in snap.get("crawl_pop_tables", [])}


def summarize(cur: dict, prev: dict | None) -> str:
    q = _quality(cur)
    pct = [r["usable_pct"] for r in q.values() if r["usable_pct"] is not None]
    lines = [f"# snapshot {cur['taken_at']}",
             f"raw 平均 usable%: {sum(pct) / len(pct):.1f}（{len(pct)} 個 json 欄位）"]
    worst = sorted((r for r in q.values() if r["usable_pct"] is not None), key=lambda r: r["usable_pct"])[:3]
    lines.append("最低三個: " + ", ".join(f'{r["raw_view"]}.{r["json_column"]} {r["usable_pct"]}%' for r in worst))
    scheduled = [r["seed_table"] for r in cur["crawl_status"] if r["scheduled"]]
    lines.append(f"有排程的 seed: {scheduled or '無'}")
    if prev is None:
        lines.append("（沒有上一份 snapshot 可比較）")
        return "\n".join(lines)
    lines.append(f"## 與 {prev['taken_at']} 比較")
    pq = _quality(prev)
    for k, r in sorted(q.items()):
        old = pq.get(k)
        if old and old["usable_pct"] is not None and r["usable_pct"] is not None \
                and abs(r["usable_pct"] - old["usable_pct"]) >= 0.1:
            lines.append(f'- {k}: usable {old["usable_pct"]}% → {r["usable_pct"]}%'
                         f'（{old["usable_rows"]}/{old["total_rows"]} → {r["usable_rows"]}/{r["total_rows"]}）')
    pr, cr = _rows(prev), _rows(cur)
    grown = {t: cr[t] - pr.get(t, 0) for t in cr if cr[t] != pr.get(t)}
    if grown:
        lines.append("- 列數變化: " + ", ".join(f"{t} {d:+d}" for t, d in sorted(grown.items())))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    previous = sorted(glob.glob(os.path.join(SNAP_DIR, "*.json")))
    prev = json.load(open(previous[-1], encoding="utf-8")) if previous else None
    cur = take()
    if not args.no_save:
        path = os.path.join(SNAP_DIR, f"{dt.date.today().isoformat()}.json")
        if prev is not None and previous[-1] == path:  # re-run on the same day: compare to the one before
            prev = json.load(open(previous[-2], encoding="utf-8")) if len(previous) > 1 else None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1, default=str)
        print(f"saved {os.path.relpath(path)}")
    print(summarize(cur, prev))


if __name__ == "__main__":
    main()
