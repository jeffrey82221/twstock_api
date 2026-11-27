"""指令 1 盤點：把 MCP tool 的原始輸出存成 snapshots/<日期>.json，並與上一份比較。

用法（repo 根目錄）：
    .venv/bin/python verify_questions/snapshot.py            # 存今天的 snapshot 並印出比較
    .venv/bin/python verify_questions/snapshot.py --no-save  # 只印比較

呼叫的是 server.py 裡的同一組 tool 函式（crawl_pop_tables、pop_raw_quality、
crawl_status、http_stats），所以內容與透過 MCP 呼叫相同。

另外對所有正規化 pop 表做 README §7.5 的維度剖析（存在 snapshot 的 `profile`）：
每欄非 NULL 列數（完整性）、依 ENTITY_KEYS 算實體重複（唯一性）、每張表最新資料日（及時性）。
查詢都經過 server.select（與 MCP 同一個唯讀白名單）。
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

# 實體鍵（README §7.1 唯一性）：同一個交易日／事件／季度／新聞／借券交易只能算一次。
# 有唯一索引的表主鍵不會重複，這裡要抓的是「同一事實被兩個 as_of 取到」與上游清單重複。
# 鍵欄位為 NULL 的列（失敗列）不算。across_as_of=True 只算出現在兩個以上 as_of 的組：
# 借券上游在同一份回應裡本來就可能列出條件完全相同的多筆交易。
ENTITY_KEYS: dict[str, tuple[str, bool]] = {
    "ohlcv_daily": ("stk_code, trade_date", False),
    "company_valuation": ("stk_code, data_date", False),
    "market_valuation_summary": ("data_date", False),
    "institutional_net_buy_sell": ("stk_code, trade_date", False),
    "foreign_ownership": ("stk_code, data_date", False),
    "monthly_revenue_twse": ("stk_code, latest_month_label", False),
    "monthly_revenue": ("stk_code, latest_month_label", False),
    "mops_quarterly_financials": ("stk_code, fiscal_year, quarter", False),
    "financial_quarterly_yfinance": ("stk_code, latest_quarter_date", False),
    "dividend": ("stk_code, reference_date", False),
    "dividend_yfinance": ("stk_code, reference_date", False),
    "finmind_news": ("stk_code, link", False),
    "product_revenue": ("stk_code, ym, rank", False),
    "company_business_items": ("stock_id, code", False),
    "sbl_history": ("stk_code, transaction_date, transaction_type, quantity_lots, fee_rate_pct, "
                    "completion_date, lending_days", True),
}

# 每張表的「實際資料日」欄位（README §7.1 及時性）。
LATEST_COLUMN = {
    "ohlcv_daily": "trade_date",
    "company_valuation": "data_date",
    "market_valuation_summary": "data_date",
    "institutional_net_buy_sell": "trade_date",
    "foreign_ownership": "data_date",
    "sbl_history": "data_date",
    "monthly_revenue_twse": "available_date",
    "monthly_revenue": "as_of",
    "mops_quarterly_financials": "available_date",
    "financial_analysis": "year",
    "financial_quarterly_yfinance": "latest_quarter_date",
    "financial_yearly_yfinance": "latest_quarter_date",
    "dividend": "reference_date",
    "dividend_yfinance": "reference_date",
    "finmind_news": "news_date",
    "product_revenue": "report_month",
}


def _profile(server) -> dict:
    cols = server.select(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'pop' AND table_name NOT LIKE 'raw\\_%' AND table_name NOT LIKE '%\\_list' "
        "ORDER BY table_name, ordinal_position", limit=5000)
    by_table: dict[str, list[str]] = {}
    for c in cols:
        by_table.setdefault(c["table_name"], []).append(c["column_name"])
    fields = {}
    for t, names in by_table.items():
        parts = ", ".join(f'count("{c}") AS "{c}"' for c in names)
        row = server.select(f'SELECT count(*) AS "__rows", {parts} FROM pop."{t}"')[0]
        fields[t] = {"rows": row.pop("__rows"), "non_null": row}
    dups = {}
    for t, (key, across) in ENTITY_KEYS.items():
        if t not in by_table:
            continue
        not_null = " AND ".join(f'"{k.strip()}" IS NOT NULL' for k in key.split(","))
        having = "count(DISTINCT as_of) > 1" if across else "count(*) > 1"
        row = server.select(
            f"SELECT count(*) AS keys, coalesce(sum(n - 1), 0) AS extra_rows FROM ("
            f"SELECT count(*) AS n FROM pop.\"{t}\" WHERE {not_null} GROUP BY {key} HAVING {having}) d")[0]
        dups[t] = {"key": key, "across_as_of": across, **row}
    latest = {}
    for t, col in LATEST_COLUMN.items():
        if t in by_table:
            latest[t] = server.select(f'SELECT max("{col}")::text AS v FROM pop."{t}"')[0]["v"]
    return {"fields": fields, "entity_dups": dups, "latest": latest}


def take() -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        import server

        return {
            "taken_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "crawl_pop_tables": server.crawl_pop_tables(),
            "pop_raw_quality": server.pop_raw_quality(),
            "crawl_status": server.crawl_status(lookback_minutes=1440),
            "http_stats": server.http_stats(minutes=1440),
            "profile": _profile(server),
        }


def _quality(snap: dict) -> dict:
    return {f'{r["raw_view"]}.{r["json_column"]}': r for r in snap.get("pop_raw_quality", [])}


def _rows(snap: dict) -> dict:
    return {r["table_name"]: r["rows"] for r in snap.get("crawl_pop_tables", [])}


def _profile_lines(cur: dict, prev: dict | None) -> list[str]:
    p = cur.get("profile")
    if not p:
        return []
    pp = (prev or {}).get("profile")
    lines = ["## 維度剖析（README §7.5）" + ("" if pp else "：上一份沒有剖析，以下是基線")]

    old_fields = pp["fields"] if pp else {}
    empty, drops, partial = [], [], 0
    for t, r in sorted(p["fields"].items()):
        n, old = r["rows"], old_fields.get(t)
        for col, nn in r["non_null"].items():
            prev_nn = old["non_null"].get(col) if old else None
            if n and nn < n:
                partial += 1
            if n and nn == 0 and (prev_nn is None or prev_nn > 0):
                empty.append(f"{t}.{col}")
            if old and old["rows"] and n and prev_nn is not None:
                a, b = 100 * prev_nn / old["rows"], 100 * nn / n
                if b <= a - 1.0:
                    drops.append(f"{t}.{col} {a:.1f}% → {b:.1f}%")
    label = "新的全空欄位" if pp else "全空欄位"
    lines.append(f"- 完整性：{label} {len(empty)} 個" + (f"：{', '.join(empty)}" if empty else ""))
    if drops:
        lines.append(f"- 完整性：非空率下降 ≥ 1 點：{', '.join(drops)}")
    if not pp:
        lines.append(f"- 完整性：部分空值欄位 {partial} 個（逐欄數字在 profile.fields，依 §7.5 分類進 LEDGER 剖析基線）")

    old_dups = pp["entity_dups"] if pp else {}
    dup = []
    for t, d in sorted(p["entity_dups"].items()):
        o = old_dups.get(t)
        if o and o["extra_rows"] == d["extra_rows"]:
            continue
        if d["extra_rows"] or o:
            was = f'{o["extra_rows"]} → ' if o else ""
            scope = "，跨 as_of" if d["across_as_of"] else ""
            dup.append(f'{t}（{d["key"]}{scope}）{d["keys"]} 組、多 {was}{d["extra_rows"]} 列')
    lines.append("- 唯一性：" + ("；".join(dup) if dup else ("實體重複無變化" if pp else "沒有實體重複")))

    latest = sorted(p["latest"].items(), key=lambda kv: kv[1] or "")
    lines.append("- 及時性：最新資料日 " + ", ".join(f"{t} {v}" for t, v in latest))

    endpoints = cur.get("http_stats", {}).get("endpoints", [])
    bad = [e for e in endpoints if e.get("server_5xx")]
    lines.append(f"- 可取得性：24h 內 API 請求 {sum(e['requests'] for e in endpoints)} 次、5xx "
                 f"{sum(e['server_5xx'] for e in bad)} 次"
                 + (f"（{', '.join(e['endpoint'] + ' ' + str(e['server_5xx']) for e in bad[:3])}）" if bad else ""))
    return lines


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
        return "\n".join(lines + _profile_lines(cur, prev))
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
    return "\n".join(lines + _profile_lines(cur, prev))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    # 一律和「最近一次」盤點比（同一天重跑時就是今天稍早那份，讀進記憶體後才覆寫）。
    # 一天跑多輪指令 7 時，和前一天比會把今天稍早的變化重複報告一次，也看不出這一輪的變化。
    previous = sorted(glob.glob(os.path.join(SNAP_DIR, "*.json")))
    prev = json.load(open(previous[-1], encoding="utf-8")) if previous else None
    cur = take()
    if not args.no_save:
        path = os.path.join(SNAP_DIR, f"{dt.date.today().isoformat()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1, default=str)
        print(f"saved {os.path.relpath(path)}")
    print(summarize(cur, prev))


if __name__ == "__main__":
    main()
