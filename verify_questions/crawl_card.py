"""指令 3 補爬：依問題卡 front matter 的 crawl 區塊，對每個 seed 先 dry run 再分批 insert。

用法（repo 根目錄，API 須在 port 5002）：
    .venv/bin/python verify_questions/crawl_card.py Q002            # 實際補爬
    .venv/bin/python verify_questions/crawl_card.py Q002 --dry-run  # 只看 pending

每個 seed 最多 insert `budget` 列，單次 row_cnt 依 README §8.3 的上游上限，
逾時（整批 rollback）時 row_cnt 減半重試。印出的表格可直接貼進 run 紀錄。
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# README §8.3：單次 row_cnt 上限（依上游資料源）
ROW_CNT_CAP = {
    "company_list": 20, "company_basic_info_list": 20,
    "company_finmind_list": 5, "dividend_event_list": 5, "financial_year_list": 5,
    "financial_month_list": 5, "finmind_news_monthly_list": 5,
    "ohlcv_monthly_list": 12, "company_valuation_monthly_list": 12, "market_valuation_date_list": 12,
    "institutional_monthly_list": 12, "foreign_ownership_monthly_list": 12, "sbl_history_monthly_list": 12,
    "mops_quarterly_financials_quarter_list": 8, "financial_analysis_year_list": 8,
    "financial_month_twse_list": 8, "product_revenue_filer_list": 8,
    "financial_year_yfinance_list": 20, "financial_quarter_yfinance_list": 20,
    "dividend_event_yfinance_list": 20,
}


def load_crawl(qid: str) -> list[dict]:
    (path,) = glob.glob(os.path.join(HERE, "questions", f"{qid}-*.md"))
    front = re.match(r"---\n(.*?)\n---\n", open(path, encoding="utf-8").read(), re.DOTALL).group(1)
    return yaml.safe_load(front).get("crawl") or []


def crawl(qid: str, dry_run: bool) -> list[dict]:
    with contextlib.redirect_stdout(sys.stderr):
        import server
    out = []
    for step in load_crawl(qid):
        seed, filters, budget = step["seed"], step["filters"], int(step["budget"])
        order_by = step.get("order_by")
        probe = server.crawl_insert_filtered(seed, filters, row_cnt=1, order_by=order_by, dry_run=True)
        rec = {"seed": seed, "filters": filters, "pending_before": probe["pending_matching"],
               "inserted": 0, "seconds": 0.0, "errors": []}
        cap = ROW_CNT_CAP.get(seed, 5)
        remaining = min(budget, probe["pending_matching"])
        t0 = time.perf_counter()
        while remaining > 0 and not dry_run:
            n = min(cap, remaining)
            try:
                r = server.crawl_insert_filtered(seed, filters, row_cnt=n, order_by=order_by,
                                                 dry_run=False, timeout_seconds=300)
            except Exception as e:  # timeout → whole batch rolled back; retry smaller
                rec["errors"].append(f"row_cnt={n}: {type(e).__name__}: {str(e)[:160]}")
                if cap == 1 or len(rec["errors"]) >= 4:
                    break
                cap = max(1, cap // 2)
                continue
            if r["inserted"] == 0:
                break
            rec["inserted"] += r["inserted"]
            remaining -= r["inserted"]
        rec["seconds"] = round(time.perf_counter() - t0, 1)
        out.append(rec)
        print(f"{seed:40s} pending={rec['pending_before']:5d} inserted={rec['inserted']:4d} "
              f"{rec['seconds']:6.1f}s {'; '.join(rec['errors'])}", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qid")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = crawl(args.qid, args.dry_run)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
