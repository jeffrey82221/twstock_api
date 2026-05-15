"""組裝查詢服務：把多個來源整合成一份回應。

任一日 (as_of) 的 TTM/年化邏輯：
- EPS / 淨利 / 營業利潤率：取 as_of 之前最近 4 季的財報數值加總（TTM）；
  並標註來源期間。如果不足 4 季就回傳 N/A。
- 月營收：取 as_of 之前最近 12 個完整月份加總，作為「年化營收」。
  另回傳當月營收 + YoY、累計營收 + YoY。
- 股利：取「除息日 ≤ as_of」的最後一次股利（現金 + 股票）。
- 資本額/總經理：使用最新基本資料（TWSE/TPEx 每日更新）。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import icchain
from .industry import industry_name
from .sources import (
    get_business_scope,
    get_dividend,
    get_financial_statements,
    get_month_revenue,
    load_basic_table,
)


def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_minguo_date(s: str) -> str:
    """日期格式化：
    - 7 位民國 1150508 → 2026-05-08
    - 8 位西元 19870221 → 1987-02-21
    - 其他原樣返回
    """
    s = (s or "").strip()
    if len(s) == 7 and s.isdigit():
        y = int(s[:3]) + 1911
        return f"{y:04d}-{s[3:5]}-{s[5:7]}"
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _build_quarter_map(rows: list[dict]) -> dict[str, dict[str, float]]:
    """財報 rows -> { '2025-03-31': {'EPS': ..., 'Revenue': ..., ...} }"""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        d = r.get("date")
        t = r.get("type")
        v = r.get("value")
        if d and t is not None and v is not None:
            out[d][t] = v
    return out


def _is_quarter_before(quarter_date: str, as_of: date) -> bool:
    qd = _parse_date(quarter_date)
    return qd is not None and qd <= as_of


def _ttm_value(quarter_map: dict[str, dict], as_of: date, field: str) -> tuple[Optional[float], list[str]]:
    """取最近 4 個有 field 的季財報加總。"""
    eligible = [
        (d, vals[field])
        for d, vals in quarter_map.items()
        if field in vals and _is_quarter_before(d, as_of)
    ]
    eligible.sort(key=lambda x: x[0], reverse=True)
    eligible = eligible[:4]
    if len(eligible) < 4:
        return None, [d for d, _ in eligible]
    return sum(v for _, v in eligible), [d for d, _ in eligible]


def _latest_quarter_value(
    quarter_map: dict[str, dict], as_of: date, field: str
) -> tuple[Optional[float], Optional[str]]:
    eligible = [
        (d, vals[field])
        for d, vals in quarter_map.items()
        if field in vals and _is_quarter_before(d, as_of)
    ]
    if not eligible:
        return None, None
    eligible.sort(key=lambda x: x[0], reverse=True)
    return eligible[0][1], eligible[0][0]


async def query(stock_id: str, as_of_str: Optional[str] = None) -> dict[str, Any]:
    stock_id = stock_id.strip()
    as_of = _parse_date(as_of_str) if as_of_str else date.today()
    if as_of is None:
        as_of = date.today()

    basic_table = await load_basic_table()
    basic = basic_table.get(stock_id)
    if not basic:
        return {
            "found": False,
            "stock_id": stock_id,
            "as_of": as_of.isoformat(),
            "error": f"查無 {stock_id} 的上市櫃基本資料（可能為興櫃或已下市）",
        }

    # FinMind 取數窗口：往前抓 5 年以確保 TTM 4 季 + 月營收 12 個月可得
    end = as_of.isoformat()
    start = (as_of - timedelta(days=365 * 5)).isoformat()

    fs_rows, mr_rows, dv_rows, business_items = await _fetch_all(stock_id, start, end, basic.get("tax_id", ""))

    # 財報 TTM
    qmap = _build_quarter_map(fs_rows)

    eps_ttm, eps_quarters = _ttm_value(qmap, as_of, "EPS")
    revenue_ttm_fs, _ = _ttm_value(qmap, as_of, "Revenue")
    net_income_ttm, ni_quarters = _ttm_value(qmap, as_of, "IncomeAfterTaxes")
    op_income_ttm, _ = _ttm_value(qmap, as_of, "OperatingIncome")

    op_margin: Optional[float] = None
    if op_income_ttm is not None and revenue_ttm_fs and revenue_ttm_fs != 0:
        op_margin = op_income_ttm / revenue_ttm_fs * 100

    latest_eps_q, latest_eps_q_date = _latest_quarter_value(qmap, as_of, "EPS")
    latest_ni_q, latest_ni_q_date = _latest_quarter_value(qmap, as_of, "IncomeAfterTaxes")

    # 月營收：最近 12 個月加總 (TTM 月營收) + 當月 YoY
    eligible_mr = [m for m in mr_rows if _parse_date(m.get("date") or "") and _parse_date(m["date"]) <= as_of]
    eligible_mr.sort(key=lambda x: x["date"], reverse=True)
    last12 = eligible_mr[:12]
    revenue_ttm_monthly = sum(m.get("revenue", 0) for m in last12) if len(last12) == 12 else None

    latest_month = eligible_mr[0] if eligible_mr else None
    latest_month_revenue = latest_month.get("revenue") if latest_month else None
    latest_month_label = (
        f"{latest_month['revenue_year']}/{latest_month['revenue_month']:02d}" if latest_month else None
    )

    # YoY 月營收成長率：找去年同月
    revenue_yoy: Optional[float] = None
    if latest_month:
        target_y = latest_month["revenue_year"] - 1
        target_m = latest_month["revenue_month"]
        for m in eligible_mr:
            if m.get("revenue_year") == target_y and m.get("revenue_month") == target_m:
                if m.get("revenue"):
                    revenue_yoy = (latest_month_revenue - m["revenue"]) / m["revenue"] * 100
                break

    # TTM 營收成長率（最近 12 月 vs 前 12 月）
    revenue_ttm_yoy: Optional[float] = None
    prev12 = eligible_mr[12:24]
    if len(last12) == 12 and len(prev12) == 12:
        prev_sum = sum(m.get("revenue", 0) for m in prev12)
        if prev_sum:
            revenue_ttm_yoy = (revenue_ttm_monthly - prev_sum) / prev_sum * 100

    # 股利：找到「除息(權)日 ≤ as_of」最後一次
    dividend = _pick_dividend(dv_rows, as_of)

    # 產業別（TWSE/TPEx 分類）
    industry = industry_name(basic.get("industry_code", ""), basic.get("market", "上市"))

    # 主要營業項目：商工 API 所營事業
    narrative_items = [b for b in business_items if b["is_narrative"]]
    category_items = [b for b in business_items if not b["is_narrative"]]

    return {
        "found": True,
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "basic": {
            "market": basic.get("market"),
            "company_name": basic.get("company_name"),
            "short_name": basic.get("short_name"),
            "english_name": basic.get("english_name"),
            "tax_id": basic.get("tax_id"),
            "paid_in_capital": basic.get("paid_in_capital"),
            "industry_code": basic.get("industry_code"),
            "industry_name": industry,
            "business_items": {
                "narrative": [b["desc"] for b in narrative_items],
                "categories": [{"code": b["code"], "desc": b["desc"]} for b in category_items],
            },
            "general_manager": basic.get("general_manager"),
            "chairman": basic.get("chairman"),
            "incorporation_date": _format_minguo_date(basic.get("incorporation_date", "")),
            "listing_date": _format_minguo_date(basic.get("listing_date", "")),
            "website": basic.get("website"),
            "address": basic.get("address"),
        },
        "eps": {
            "ttm": eps_ttm,
            "ttm_quarters": eps_quarters,
            "latest_quarter_value": latest_eps_q,
            "latest_quarter_date": latest_eps_q_date,
        },
        "revenue": {
            "latest_month_label": latest_month_label,
            "latest_month_value": latest_month_revenue,
            "latest_month_yoy_pct": revenue_yoy,
            "ttm_value": revenue_ttm_monthly,
            "ttm_yoy_pct": revenue_ttm_yoy,
            "ttm_from_financial_statements": revenue_ttm_fs,
        },
        "net_income": {
            "ttm": net_income_ttm,
            "ttm_quarters": ni_quarters,
            "latest_quarter_value": latest_ni_q,
            "latest_quarter_date": latest_ni_q_date,
        },
        "operating_margin_pct": op_margin,
        "dividend": dividend,
        "sources": [
            "TWSE OpenAPI (上市公司基本資料)",
            "TPEx OpenAPI (上櫃公司基本資料)",
            "FinMind v4 (TaiwanStockFinancialStatements / MonthRevenue / Dividend)",
            "經濟部商工 (公司登記基本資料 - 所營事業)",
            "櫃買中心 產業價值鏈資訊平台 (上中下游定位)",
        ],
        "value_chain": _build_value_chain_section(stock_id),
    }


async def _fetch_all(stock_id: str, start: str, end: str, tax_id: str):
    import asyncio

    # 同時觸發產業價值鏈背景載入（首次）
    asyncio.create_task(icchain.ensure_loaded(background=True))

    return await asyncio.gather(
        get_financial_statements(stock_id, start, end),
        get_month_revenue(stock_id, start, end),
        get_dividend(stock_id, start, end),
        get_business_scope(tax_id),
    )


def _build_value_chain_section(stock_id: str) -> dict:
    """組裝公司的上中下游資訊。

    回傳結構：
    {
      status: 'ready' | 'loading',
      memberships: [{ic_code, ic_name, segment, top_code, top_name, sub_code, sub_name}],
      neighbors_by_chain: {
        ic_code: {
          ic_name,
          upstream: [{stk_code, name}],
          midstream: [...],
          downstream: [...],
        }
      }
    }
    """
    if not icchain.is_loaded():
        return {
            "status": "loading" if icchain.is_loading() else "unavailable",
            "memberships": [],
            "neighbors_by_chain": {},
        }

    memberships = icchain.get_memberships(stock_id)
    neighbors: dict[str, dict] = {}
    seen_chain_codes = {m["ic_code"] for m in memberships}
    for ic_code in seen_chain_codes:
        chain = icchain.get_chain(ic_code) or {}
        if not chain:
            continue
        seg_buckets = {"upstream": [], "midstream": [], "downstream": []}
        seg_map = {"上游": "upstream", "中游": "midstream", "下游": "downstream"}
        for seg_zh, tops in (chain.get("segments") or {}).items():
            key = seg_map.get(seg_zh)
            if not key:
                continue
            for top in tops:
                for sub in top.get("sub_chains", []):
                    for c in sub.get("companies", []):
                        seg_buckets[key].append({
                            "stk_code": c["stk_code"],
                            "name": c["name"],
                            "top_name": top["top_name"],
                            "sub_name": sub["sub_name"],
                            "is_self": c["stk_code"] == stock_id,
                        })
        # 去重（同一公司可能在多個 sub-chain 出現）
        for key, lst in seg_buckets.items():
            seen = set()
            dedup = []
            for c in lst:
                if c["stk_code"] in seen:
                    continue
                seen.add(c["stk_code"])
                dedup.append(c)
            seg_buckets[key] = dedup
        neighbors[ic_code] = {
            "ic_name": chain.get("ic_name", ""),
            **seg_buckets,
        }

    return {
        "status": "ready",
        "memberships": memberships,
        "neighbors_by_chain": neighbors,
    }


def _pick_dividend(rows: list[dict], as_of: date) -> Optional[dict]:
    """找最後一次除息日 ≤ as_of 的股利。"""
    candidates = []
    for r in rows:
        ex_cash = _parse_date(r.get("CashExDividendTradingDate") or "")
        ex_stock = _parse_date(r.get("StockExDividendTradingDate") or "")
        ann = _parse_date(r.get("date") or "")
        # 取「除息日」優先；若空，用公告日
        ref = ex_cash or ex_stock or ann
        if ref and ref <= as_of:
            candidates.append((ref, r))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    ref_date, r = candidates[0]
    return {
        "year": r.get("year"),
        "reference_date": ref_date.isoformat(),
        "cash_dividend": r.get("CashEarningsDistribution", 0) + r.get("CashStatutorySurplus", 0),
        "stock_dividend": r.get("StockEarningsDistribution", 0) + r.get("StockStatutorySurplus", 0),
        "cash_ex_dividend_date": r.get("CashExDividendTradingDate"),
        "cash_payment_date": r.get("CashDividendPaymentDate"),
        "stock_ex_dividend_date": r.get("StockExDividendTradingDate"),
        "announcement_date": r.get("AnnouncementDate"),
    }


async def search_companies(keyword: str, limit: int = 20) -> list[dict]:
    """簡單搜尋：依代號或名稱關鍵字。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    table = await load_basic_table()
    results = []
    for code, info in table.items():
        if (
            keyword in code
            or keyword in info.get("company_name", "")
            or keyword in info.get("short_name", "")
            or keyword.upper() in (info.get("english_name") or "").upper()
        ):
            results.append({
                "stock_id": code,
                "company_name": info.get("company_name"),
                "short_name": info.get("short_name"),
                "market": info.get("market"),
                "industry_name": industry_name(info.get("industry_code", ""), info.get("market", "上市")),
            })
            if len(results) >= limit:
                break
    return results
