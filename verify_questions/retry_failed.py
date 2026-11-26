"""Re-crawl seed rows whose stored API response is an upstream failure.

Seed rows are frozen once inserted: if the API failed at crawl time (TWSE block page,
FinMind quota, timeout), the raw IMMV keeps that failure forever. This finds them --
response NULL (the endpoint answered 5xx) or carrying `source_errors` (the endpoint
answered 200 but an upstream call failed) -- deletes them with the same checks as the
`crawl_delete_rows` MCP tool (README §10.3: responses pinned, 1:1 IMMVs verified, rollback
on orphans) and inserts the same keys again from hidden.<seed>.

    python verify_questions/retry_failed.py                    # dry run: counts per raw view
    python verify_questions/retry_failed.py raw_sbl_history --apply
    python verify_questions/retry_failed.py --apply --batch 5 --pause 2

Needs the API (port 5002) running. Rows are retried per stock, `--batch` keys per insert.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pop_maintenance  # noqa: E402
from pg_tool import PostgreSQLTool  # noqa: E402
from pipeline import Pipeline  # noqa: E402

# raw view -> (json column, seed, {seed key column: raw column})
RAW = {
    "raw_institutional_net_buy_sell": ("institutional_net_buy_sell", "institutional_monthly_list",
                                       {"stk_code": "stk_code", "month_start_date": "as_of"}),
    "raw_sbl_history": ("sbl_history", "sbl_history_monthly_list",
                        {"stk_code": "stk_code", "month_start_date": "as_of"}),
    "raw_company_valuation": ("company_valuation", "company_valuation_monthly_list",
                              {"stk_code": "stk_code", "valuation_date": "as_of"}),
    "raw_foreign_ownership": ("foreign_ownership", "foreign_ownership_monthly_list",
                              {"stk_code": "stk_code", "month_start_date": "as_of"}),
    "raw_financial_analysis": ("financial_analysis", "financial_analysis_year_list",
                               {"stk_code": "stk_code", "year": "year"}),
    "raw_mops_quarterly_financials": ("quarterly_financials", "mops_quarterly_financials_quarter_list",
                                      {"stk_code": "stk_code", "as_of_date": "as_of"}),
    "raw_monthly_revenue": ("revenue", "financial_month_list",
                            {"stk_code": "stk_code", "month_start_date": "as_of"}),
    "raw_monthly_revenue_twse": ("revenue", "financial_month_twse_list",
                                 {"stk_code": "stk_code", "as_of_date": "as_of"}),
    "raw_yearly_financials": ("financials", "financial_year_list",
                              {"stk_code": "stk_code", "year_start_date": "as_of"}),
    "raw_dividend": ("dividend", "dividend_event_list",
                     {"stk_code": "stk_code", "reference_date": "as_of"}),
    "raw_dividend_history": ("dividend_history", "company_finmind_list", {"stk_code": "stk_code"}),
    "raw_dividend_history_yfinance": ("dividend_history", "company_list", {"stk_code": "stk_code"}),
    "raw_dividend_yfinance": ("dividend", "dividend_event_yfinance_list",
                              {"stk_code": "stk_code", "reference_date": "as_of"}),
    "raw_quarterly_financials_yfinance": ("financials", "financial_quarter_yfinance_list",
                                          {"stk_code": "stk_code", "quater": "as_of"}),
    "raw_yearly_financials_yfinance": ("financials", "financial_year_yfinance_list",
                                       {"stk_code": "stk_code", "year_start_date": "as_of"}),
    "raw_finmind_news": ("finmind_news", "finmind_news_monthly_list",
                         {"stk_code": "stk_code", "month_start_date": "as_of"}),
    "raw_product_revenue": ("product_revenue", "product_revenue_filer_list",
                            {"stk_code": "stk_code", "ym": "ym", "report_month": "report_month"}),
    "raw_ohlcv_monthly": ("ohlcv", "ohlcv_monthly_list",
                          {"stk_code": "stk_code", "month_start_date": "month_start_date"}),
}

# What counts as "said no data" for --include-not-found, when it is not `found = false`.
NOT_FOUND_SQL = {
    "raw_ohlcv_monthly": "jsonb_array_length(COALESCE(\"ohlcv\"->'rows', '[]'::jsonb)) = 0",
}


def failed_keys(cur, raw: str, include_not_found: bool = False) -> list[dict]:
    col, _seed, keys = RAW[raw]
    select = ", ".join(f'"{r}" AS "{s}"' for s, r in keys.items())
    where = f'"{col}" IS NULL OR "{col}" ? \'source_errors\''
    if include_not_found:   # after fixing a parser bug: found=false answers were wrong too
        where += " OR " + NOT_FOUND_SQL.get(raw, f'"{col}"->>\'found\' = \'false\'')
    cur.execute(f'SELECT {select} FROM pop."{raw}" WHERE {where}')
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def reinsert(conn, seed: str, rows: list[dict]) -> int:
    cols = list(rows[0])
    cond = " OR ".join("(" + " AND ".join(f'h."{c}" = %s' for c in cols) + ")" for _ in rows)
    params = [r[c] for r in rows for c in cols]
    with conn.cursor() as cur:
        cur.execute(f'INSERT INTO pop."{seed}" SELECT h.* FROM hidden."{seed}" h WHERE ({cond}) '
                    f'EXCEPT SELECT * FROM pop."{seed}"', params)
        n = cur.rowcount
    conn.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raws", nargs="*", help=f"raw views (default: all of {', '.join(RAW)})")
    ap.add_argument("--apply", action="store_true", help="delete and re-insert (default: dry run)")
    ap.add_argument("--batch", type=int, default=5, help="keys per delete / insert transaction")
    ap.add_argument("--pause", type=float, default=1.0, help="seconds between transactions")
    ap.add_argument("--include-not-found", action="store_true",
                    help="also retry found=false answers (use after fixing an adapter that wrongly said 'no data')")
    args = ap.parse_args()

    pipeline = Pipeline(setup_db=False)
    conn = PostgreSQLTool().get_conn()
    for raw in args.raws or list(RAW):
        _col, seed, keys = RAW[raw]
        with conn.cursor() as cur:
            rows = failed_keys(cur, raw, args.include_not_found)
        conn.commit()
        print(f"{raw}: {len(rows)} failed rows (seed {seed})", flush=True)
        if not args.apply or not rows:
            continue
        by_stock: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_stock[r["stk_code"]].append(r)
        fixed = failed = 0
        for code, group in by_stock.items():
            for i in range(0, len(group), args.batch):
                chunk = group[i:i + args.batch]
                filters = {"stk_code": code}
                for c in keys:
                    if c != "stk_code":
                        filters[c] = [str(r[c]) for r in chunk]
                out = pop_maintenance.delete_seed_rows(conn, pipeline, seed, filters, dry_run=False)
                if out.get("result") != "deleted":
                    print(f"  {code} {filters}: delete {out.get('result')} {out.get('reason', '')}", flush=True)
                    failed += len(chunk)
                    continue
                try:
                    fixed += reinsert(conn, seed, chunk)
                except Exception as e:  # noqa: BLE001 -- keep going; the row stays deleted and shows up as missing
                    conn.rollback()
                    failed += len(chunk)
                    print(f"  {code}: re-insert failed: {str(e).splitlines()[0][:150]}", flush=True)
                time.sleep(args.pause)
        with conn.cursor() as cur:
            left = len(failed_keys(cur, raw, args.include_not_found))
        conn.commit()
        print(f"{raw}: re-inserted {fixed}, errors {failed}, still failed after retry {left}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
