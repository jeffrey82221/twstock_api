"""
Seed fill-time and storage estimation for db/poc/* — measured from the live DB.

舊版腳本以「1,900 家 × 300 月」之類的假設值推算；本版改為從 live DB 實測：

- 母體列數：直接 count `hidden.<seed>`（= 目前 SQL 定義下的母體），再依「上游已爬到多少」
  換算成全量（例：financial_month_list 的 hidden 只看得到已爬到的公司，乘上
  全量公司數 / 已爬公司數）。
- 已爬列數：`pop.<seed>` 中仍落在新母體內的列（hidden ∩ pop），避免把改邊界前爬過、
  已不在母體內的舊列算進進度。
- 速率：讀 `cron.job` 的實際排程（schedule + 指令內的 LIMIT）。
- 存放：seed 已爬 ≥ MIN_ROWS_FOR_MEASURE 列時，以「該 seed 下游所有 pop 表
  （直到下一個 seed 為止）的 pg_total_relation_size ÷ seed 已爬列數」為每列實測成本；
  不足時用 FALLBACK_ROW_BYTES（依相近 endpoint 的實測 payload 推估，表內標註「推估」）。

用法（repo root）：
    .venv/bin/python db/poc/scripts/estimate_seed_fill_and_storage.py > /tmp/estimate.md

注意：數字為 PoC 估算。填滿時間假設排程穩定、不計 tick 失敗重試；實際可用
MCP `crawl_status` 的 rows_per_hour 校正。
"""
from __future__ import annotations

import os
import re
import sys
from collections import deque
from datetime import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)

MIN_ROWS_FOR_MEASURE = 50  # seed 已爬列數低於此值時，表固定開銷 (≥16 KB/表) 會讓每列成本失真
OBSERVE_MINUTES = int(os.environ.get("ESTIMATE_OBSERVE_MINUTES", "30"))  # 實測速率窗口（分鐘）

# 下游無實測資料時的每 seed 列成本（raw JSONB + 正規化表 + 索引），依相近 endpoint 實測推估。
FALLBACK_ROW_BYTES = {
    "financial_month_list": 900,            # 同 raw_monthly_revenue_twse（payload ~340 B）
    "financial_month_twse_list": 900,
    "financial_year_list": 1_500,           # 同 raw_yearly_financials_yfinance 的實測量級
    "financial_analysis_year_list": 1_800,  # 21 欄財務比率
    "sbl_history_monthly_list": 2_000,      # records 陣列攤平
    "finmind_news_monthly_list": 2_000,     # items 陣列攤平
    "foreign_ownership_monthly_list": 900,
    "institutional_monthly_list": 700,
    "dividend_event_list": 900,
    "dividend_event_yfinance_list": 900,
    "mops_quarterly_financials_quarter_list": 1_200,
    "company_valuation_monthly_list": 1_200,
    "financial_quarter_yfinance_list": 1_200,
    "financial_year_yfinance_list": 1_500,
}
DEFAULT_FALLBACK_ROW_BYTES = 1_000
SEED_ROW_BYTES_FALLBACK = 100  # seed 本體（heap tuple + PK index）

# 主要上游資料源（說明用；限流判斷見 README「對作業方針的意義」）
SOURCE = {
    "chain_list": "IC 產業鏈",
    "chain_info_list": "—（純 SQL 展開）",
    "company_list": "TWSE/TPEx OpenAPI + 經濟部 + yfinance",
    "company_finmind_list": "FinMind",
    "company_basic_info_list": "—（純 SQL 正規化）",
    "dividend_event_list": "FinMind",
    "dividend_event_yfinance_list": "yfinance",
    "financial_year_list": "FinMind",
    "financial_year_yfinance_list": "yfinance",
    "financial_quarter_yfinance_list": "yfinance",
    "financial_month_list": "FinMind",
    "financial_month_twse_list": "TWSE OpenAPI + MOPS t21sc03",
    "financial_analysis_year_list": "MOPS t51sb02",
    "mops_quarterly_financials_quarter_list": "MOPS t163sb06/04",
    "market_valuation_date_list": "TWSE BWIBBU_d",
    "company_valuation_monthly_list": "TWSE BWIBBU_d（與上列共用日 cache）",
    "ohlcv_monthly_list": "TWSE STOCK_DAY / TPEx",
    "institutional_monthly_list": "TWSE T86 / TPEx",
    "foreign_ownership_monthly_list": "TWSE MI_QFIIS",
    "sbl_history_monthly_list": "TWSE t13sa870",
    "finmind_news_monthly_list": "FinMind",
    "product_revenue_filer_scope_list": "MOPS ajax_t05st08_all",
    "product_revenue_filer_list": "MOPS t05st08",
}


def _q(db, sql, params=None):
    return db.fetch_all(sql, params)


def _one(db, sql, params=None):
    return _q(db, sql, params)[0][0]


def _downstream_relations(pipeline, seed: str) -> list[str]:
    """seed 的下游 pop 物件（BFS，遇到下一個 seed 就停）。"""
    out, seen = [], set()
    dq = deque([seed + ".sql"])
    while dq:
        node = dq.popleft()
        for nxt in pipeline.dag.successors(node):
            name = nxt[: -len(".sql")]
            if nxt in seen or name.endswith("_list"):
                continue
            seen.add(nxt)
            out.append(name)
            dq.append(nxt)
    return out


def _parse_schedule(schedule: str) -> float | None:
    """回傳 period 秒數。支援 'N seconds'、'*/M * * * *'、'M H * * *'（每日一次）。"""
    m = re.fullmatch(r"(\d+) seconds?", schedule.strip())
    if m:
        return float(m.group(1))
    parts = schedule.split()
    if len(parts) == 5:
        mi, hr = parts[0], parts[1]
        m = re.fullmatch(r"\*/(\d+)", mi)
        if m and hr == "*":
            return float(m.group(1)) * 60
        if mi == "*" and hr == "*":
            return 60.0
        if mi.isdigit() and hr.isdigit():
            return 86400.0
        m = re.fullmatch(r"\*/(\d+)", hr)
        if mi.isdigit() and m:
            return float(m.group(1)) * 3600
    return None


def _schedules(db) -> dict[str, tuple[float, int]]:
    out = {}
    for jobname, schedule, command, active in _q(
        db, "SELECT jobname, schedule, command, active FROM cron.job WHERE jobname LIKE 'seed_refresh_%'"
    ):
        if not active:
            continue
        table = re.sub(r"\d+$", "", jobname[len("seed_refresh_"):])
        m = re.search(r"LIMIT (\d+)", command)
        period = _parse_schedule(schedule)
        if m and period:
            out[table] = (period, int(m.group(1)))
    return out


def _observed_rph(db, minutes: int) -> dict[str, tuple[float, int, int]]:
    """近 N 分鐘 cron 實際新增列數換算的 rows/hr，以及成功 / 失敗次數。"""
    out = {}
    for jobname, inserted, ok, failed in _q(db, f"""
        SELECT j.jobname,
               COALESCE(sum(NULLIF(substring(d.return_message FROM 'INSERT 0 (\\d+)'), '')::int), 0),
               count(*) FILTER (WHERE d.status = 'succeeded'),
               count(*) FILTER (WHERE d.status = 'failed')
        FROM cron.job_run_details d JOIN cron.job j ON j.jobid = d.jobid
        WHERE j.jobname LIKE 'seed_refresh_%%' AND d.start_time > now() - interval '{minutes} minutes'
        GROUP BY j.jobname"""):
        table = re.sub(r"\d+$", "", jobname[len("seed_refresh_"):])
        out[table] = (inserted, ok, failed)
    # 主機睡眠時 Docker VM 暫停、cron 不會執行；以「窗口內實際有 cron 執行的分鐘數」當分母，
    # 避免睡眠空檔稀釋速率。
    active_minutes = _one(db, f"""
        SELECT count(DISTINCT date_trunc('minute', start_time)) FROM cron.job_run_details
        WHERE start_time > now() - interval '{minutes} minutes'""") or 1
    return {t: (ins * 60.0 / active_minutes, ok, failed) for t, (ins, ok, failed) in out.items()}


def human_dur(hours: float | None) -> str:
    if hours is None:
        return "未排程"
    if hours < 1:
        return f"{hours * 60:.0f} 分"
    if hours < 48:
        return f"{hours:.1f} 小時"
    days = hours / 24
    if days < 60:
        return f"{days:.1f} 天"
    return f"{days:.0f} 天 (~{days / 30:.1f} 月)"


def human_bytes(b: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def main() -> None:
    import contextlib
    import io

    with contextlib.redirect_stdout(io.StringIO()):  # Pipeline 建 DAG 時很吵
        from pipeline import Pipeline

        p = Pipeline(setup_db=False)
        seeds = list(p.seed_tables)
        upstream = {s: p.get_upstream_seed_tables(s) for s in seeds}
        downstream = {s: _downstream_relations(p, s) for s in seeds}
    db = p._db_tool

    # ---- 全量公司數與換算基準 ----
    universe = _one(db, "SELECT count(DISTINCT stk_code) FROM pop.chain_info_list")
    raw_ci = _one(db, "SELECT count(*) FROM pop.raw_company_info")
    pop_basic = _one(db, "SELECT count(*) FROM pop.company_basic_info_list")
    found_ratio = pop_basic / raw_ci if raw_ci else 1.0
    est_basic = universe * found_ratio
    yf_cos = _one(db, "SELECT count(DISTINCT stk_code) FROM pop.raw_yearly_financials_yfinance")
    div_cos = _one(db, "SELECT count(*) FROM pop.raw_dividend_history")
    divyf_cos = _one(db, "SELECT count(*) FROM pop.raw_dividend_history_yfinance")

    def scale_of(seed: str) -> tuple[float, str]:
        if seed in ("company_list",):
            return 1.0, "全量（chain_info_list DISTINCT stk_code）"
        if seed == "company_finmind_list":
            return universe / max(_one(db, "SELECT count(*) FROM pop.company_list"), 1), "× 全量公司 / 已爬 company_list"
        if seed == "company_basic_info_list":
            return est_basic / max(pop_basic, 1), f"全量公司 × 基本資料可得率 {found_ratio:.0%}"
        if seed == "dividend_event_list":
            return universe / max(div_cos, 1), "× 全量公司 / 已爬 raw_dividend_history"
        if seed == "dividend_event_yfinance_list":
            return universe / max(divyf_cos, 1), "× 全量公司 / 已爬 raw_dividend_history_yfinance"
        if seed == "financial_quarter_yfinance_list":
            return est_basic / max(yf_cos, 1), "× 估計公司數 / 已爬 yfinance 年報公司"
        if "company_basic_info_list" in upstream[seed]:
            return est_basic / max(pop_basic, 1), "× 估計公司數 / 已爬 company_basic_info_list"
        return 1.0, "全量（純日期產生器或上游已全量）"

    sched = _schedules(db)
    observed = _observed_rph(db, OBSERVE_MINUTES)

    rows = []
    for seed in seeds:
        if seed == "chain_list":  # hidden.chain_list 會打 API
            hidden_n = pop_n_in = _one(db, "SELECT count(*) FROM pop.chain_list")
        else:
            hidden_n = _one(db, f"SELECT count(*) FROM hidden.{seed}")
            pop_n_in = _one(db, f"SELECT count(*) FROM (SELECT * FROM hidden.{seed} INTERSECT SELECT * FROM pop.{seed}) x")
        pop_n = _one(db, f"SELECT count(*) FROM pop.{seed}")
        factor, method = scale_of(seed)
        full = round(hidden_n * factor)
        if seed == "company_basic_info_list":  # hidden 含查無基本資料的公司；cron 只灌非 NULL 列
            full = round(est_basic)
        remaining = max(full - pop_n_in, 0)

        period, row_cnt = sched.get(seed, (None, None))
        nominal = row_cnt * 3600 / period if period else None
        obs_rph, ok_runs, failed_runs = observed.get(seed, (None, 0, 0))
        # 實測優先：窗口內有足夠執行次數、且確實還在新增列（尚未爬完）時採用實測速率
        if nominal and obs_rph and ok_runs + failed_runs >= 5 and remaining > 0:
            rph, rate_basis = min(obs_rph, nominal), "實測"
        else:
            rph, rate_basis = nominal, "名目"
        own_hours = (remaining / rph) if rph else (0.0 if remaining == 0 else None)
        fail_pct = failed_runs / (ok_runs + failed_runs) if ok_runs + failed_runs else None

        # 存放：實測 or 推估
        rel_bytes = sum(
            _one(db, "SELECT COALESCE(pg_total_relation_size(to_regclass(%s)), 0)", (f"pop.{r}",))
            for r in downstream[seed]
        )
        seed_bytes = _one(db, "SELECT COALESCE(pg_total_relation_size(to_regclass(%s)), 0)", (f"pop.{seed}",))
        if pop_n >= MIN_ROWS_FOR_MEASURE and downstream[seed]:
            per_row, basis = rel_bytes / pop_n, "實測"
        elif not downstream[seed]:
            per_row, basis = 0.0, "—"
        else:
            per_row, basis = FALLBACK_ROW_BYTES.get(seed, DEFAULT_FALLBACK_ROW_BYTES), "推估"
        seed_row = seed_bytes / pop_n if pop_n >= MIN_ROWS_FOR_MEASURE else SEED_ROW_BYTES_FALLBACK
        rows.append(dict(
            seed=seed, hidden=hidden_n, full=full, method=method, done=pop_n_in, remaining=remaining,
            period=period, row_cnt=row_cnt, rph=rph, rate_basis=rate_basis, nominal=nominal,
            fail_pct=fail_pct, own_hours=own_hours, per_row=per_row, basis=basis,
            storage=full * (per_row + seed_row), source=SOURCE.get(seed, ""),
        ))

    # 含上游：下游 seed 最快也要等上游填滿
    by = {r["seed"]: r for r in rows}

    def eff_hours(seed, stack=()):
        r = by[seed]
        if r["own_hours"] is None:
            return None
        ups = [eff_hours(u, stack + (seed,)) for u in upstream[seed] if u not in stack]
        if any(u is None for u in ups):
            return None
        return max([r["own_hours"]] + ups)

    for r in rows:
        r["eff_hours"] = eff_hours(r["seed"])

    print(f"> 量測時間：{datetime.now():%Y-%m-%d %H:%M}；全量公司 {universe:,} 家，"
          f"基本資料可得率 {found_ratio:.1%}（估計 {est_basic:,.0f} 家）。\n")
    print("| seed | 上游資料源 | 母體（全量估計） | 已爬 | 排程 | rows/hr（名目 → 估算採用） | tick 失敗率 | 自身填滿 | 含上游填滿 | 每列存放 | 填滿後存放 |")
    print("| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    total = 0
    for r in sorted(rows, key=lambda r: (r["eff_hours"] is None, -(r["eff_hours"] or 0))):
        total += r["storage"]
        sch = f"{r['row_cnt']} 列 / {r['period']:.0f}s" if r["period"] else "—"
        if r["nominal"] and r["rate_basis"] == "實測":
            rph = f"{r['nominal']:,.0f} → {r['rph']:,.0f}（實測）"
        elif r["nominal"]:
            rph = f"{r['nominal']:,.0f}"
        else:
            rph = "—"
        fp = f"{r['fail_pct']:.0%}" if r["fail_pct"] is not None else "—"
        per = f"{human_bytes(r['per_row'])}（{r['basis']}）" if r["basis"] != "—" else "—"
        print(f"| `{r['seed']}` | {r['source']} | {r['full']:,} | {r['done']:,} | {sch} | {rph} | {fp} | "
              f"{human_dur(r['own_hours'])} | {human_dur(r['eff_hours'])} | {per} | {human_bytes(r['storage'])} |")
    print()
    current = _one(db, "SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c "
                       "JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE n.nspname = 'pop' AND c.relkind = 'r'")
    print(f"**填滿後 pop 總存放估計**：約 {human_bytes(total)}（目前實際 {human_bytes(current)}）")
    print()
    print("母體換算方式：")
    for r in rows:
        if r["full"] != r["hidden"]:
            print(f"- `{r['seed']}`：hidden {r['hidden']:,} 列 {r['method']} → {r['full']:,}")


if __name__ == "__main__":
    main()
