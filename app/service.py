"""組裝查詢服務。

本檔將原本「聚合」邏輯拆成 6 個獨立、可單獨呼叫的 query_* 函式，
分別對應 6 個資料源 / 6 個 endpoint。原 `query()` 仍存在，內部直接平行呼叫
這 6 個函式，回傳和過去結構完全相容的聚合 dict（前端 / 既有用戶不需改動）。

6 個函式：
- query_basic                 → TWSE / TPEx OpenAPI 公司基本資料
- query_business_items        → 經濟部商工 GCIS 所營事業
- query_financials            → FinMind TaiwanStockFinancialStatements（EPS / 淨利 / 營業利潤率）
- query_revenue               → FinMind TaiwanStockMonthRevenue（月營收 / YoY / TTM）
- query_dividend              → FinMind TaiwanStockDividend
- query_value_chain           → 櫃買中心 ic.tpex.org.tw（產業鏈定位 + 鄰居公司）
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import icchain
from .industry import industry_name
from .sources import (
    ensure_source_errors_buffer,
    get_business_scope,
    get_dividend,
    get_financial_statements,
    get_month_revenue,
    get_product_revenue,
    get_product_revenue_at,
    get_source_errors,
    load_basic_table,
)


def with_source_error_tracking(func):
    """Decorator：在進入 query 時 reset，出口時自動裝上 source_errors / 調整 found。

    使用方式：
        @with_source_error_tracking
        async def query_xxx(...): ...
    """
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        # 只有最外層初始化 buffer；內層嵌套時不重設，不以免清掉所有來源錯誤
        is_outer = ensure_source_errors_buffer()
        result = await func(*args, **kwargs)
        if is_outer and isinstance(result, dict):
            return _apply_source_errors(result)
        return result

    return wrapper


def _apply_source_errors(result: dict[str, Any]) -> dict[str, Any]:
    """若本次 request 期間累計到來源端錯誤，則強制把 found 設成 False 並附帶診斷。

    設計重點：
    - source_errors 以 list 輸出，保留所有 status / message，供前端與使用者現關。
    - error 文字只描述「什麼來源被拒」，不覆蓋原來 service 內部產生的 error
      （例如「查無基本資料」）。
    - 原計算欄位保留，只動 found 來讓調用者能明確區分「真查無」與「來源取不到」。
    """
    errs = get_source_errors()
    if not errs:
        return result
    result["source_errors"] = [e.to_dict() for e in errs]
    result["found"] = False
    rl = any(e.is_rate_limited for e in errs)
    sources = ", ".join(sorted({e.source for e in errs}))
    if "error" not in result or not result.get("error"):
        if rl:
            result["error"] = f"來源 {sources} 遭反爬​/​限流拒絕，本次取不到資料。"
        else:
            result["error"] = f"來源 {sources} 查詢失敗，本次取不到資料。"
    return result


# =====================================================================
# 共用工具
# =====================================================================
def _parse_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_as_of(as_of_str: Optional[str]) -> date:
    as_of = _parse_date(as_of_str) if as_of_str else date.today()
    return as_of or date.today()


def _format_minguo_date(s: str) -> str:
    """日期格式化：7 位民國 1150508 → 2026-05-08；8 位西元 19870221 → 1987-02-21。"""
    s = (s or "").strip()
    if len(s) == 7 and s.isdigit():
        y = int(s[:3]) + 1911
        return f"{y:04d}-{s[3:5]}-{s[5:7]}"
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _build_quarter_map(rows: list[dict]) -> dict[str, dict[str, float]]:
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


async def _get_basic(stock_id: str) -> Optional[dict]:
    """共用：取得 TWSE/TPEx 合併後的基本資料。"""
    table = await load_basic_table()
    return table.get(stock_id)


def _finmind_window(as_of: date) -> tuple[str, str]:
    """FinMind 取數窗口：往前 5 年確保 TTM 4 季 + 月營收 24 月皆可得。"""
    return (as_of - timedelta(days=365 * 5)).isoformat(), as_of.isoformat()


# =====================================================================
# 1) /api/company/{stock_id}/basic — TWSE / TPEx 基本資料
# =====================================================================
@with_source_error_tracking
async def query_basic(stock_id: str) -> dict[str, Any]:
    stock_id = stock_id.strip()
    basic = await _get_basic(stock_id)
    if not basic:
        return {
            "found": False,
            "stock_id": stock_id,
            "error": f"查無 {stock_id} 的上市櫃基本資料（可能為興櫃或已下市）",
        }
    return {
        "found": True,
        "stock_id": stock_id,
        "market": basic.get("market"),
        "company_name": basic.get("company_name"),
        "short_name": basic.get("short_name"),
        "english_name": basic.get("english_name"),
        "tax_id": basic.get("tax_id"),
        "paid_in_capital": basic.get("paid_in_capital"),
        "industry_code": basic.get("industry_code"),
        "industry_name": industry_name(basic.get("industry_code", ""), basic.get("market", "上市")),
        "general_manager": basic.get("general_manager"),
        "chairman": basic.get("chairman"),
        "incorporation_date": _format_minguo_date(basic.get("incorporation_date", "")),
        "listing_date": _format_minguo_date(basic.get("listing_date", "")),
        "website": basic.get("website"),
        "address": basic.get("address"),
        "source": "TWSE OpenAPI t187ap03_L / TPEx OpenAPI mopsfin_t187ap03_O",
    }


# =====================================================================
# 2) /api/company/{stock_id}/business-items — 經濟部商工 所營事業
# =====================================================================
@with_source_error_tracking
async def query_business_items(stock_id: str) -> dict[str, Any]:
    stock_id = stock_id.strip()
    basic = await _get_basic(stock_id)
    if not basic:
        return {
            "found": False,
            "stock_id": stock_id,
            "error": f"查無 {stock_id} 的上市櫃基本資料",
        }
    tax_id = basic.get("tax_id", "")
    items = await get_business_scope(tax_id) if tax_id else []
    narrative = [b for b in items if b["is_narrative"]]
    categories = [b for b in items if not b["is_narrative"]]
    return {
        "found": True,
        "stock_id": stock_id,
        "tax_id": tax_id,
        "narrative": [b["desc"] for b in narrative],
        "categories": [{"code": b["code"], "desc": b["desc"]} for b in categories],
        "source": "經濟部商工登記公示資料 · 公司登記基本資料 (236EE382-...025E7C)",
    }


# =====================================================================
# 3) /api/company/{stock_id}/financials — FinMind 季財報衍生
# =====================================================================
@with_source_error_tracking
async def query_financials(stock_id: str, as_of_str: Optional[str] = None) -> dict[str, Any]:
    stock_id = stock_id.strip()
    as_of = _resolve_as_of(as_of_str)
    start, end = _finmind_window(as_of)

    fs_rows = await get_financial_statements(stock_id, start, end)
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

    return {
        "found": True,
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "eps": {
            "ttm": eps_ttm,
            "ttm_quarters": eps_quarters,
            "latest_quarter_value": latest_eps_q,
            "latest_quarter_date": latest_eps_q_date,
        },
        "net_income": {
            "ttm": net_income_ttm,
            "ttm_quarters": ni_quarters,
            "latest_quarter_value": latest_ni_q,
            "latest_quarter_date": latest_ni_q_date,
        },
        "operating_margin_pct": op_margin,
        "revenue_ttm_from_financial_statements": revenue_ttm_fs,
        "source": "FinMind v4 TaiwanStockFinancialStatements",
    }


# =====================================================================
# 4) /api/company/{stock_id}/revenue — FinMind 月營收
# =====================================================================
@with_source_error_tracking
async def query_revenue(stock_id: str, as_of_str: Optional[str] = None) -> dict[str, Any]:
    stock_id = stock_id.strip()
    as_of = _resolve_as_of(as_of_str)
    start, end = _finmind_window(as_of)

    mr_rows = await get_month_revenue(stock_id, start, end)
    eligible_mr = [m for m in mr_rows if _parse_date(m.get("date") or "") and _parse_date(m["date"]) <= as_of]
    eligible_mr.sort(key=lambda x: x["date"], reverse=True)

    last12 = eligible_mr[:12]
    prev12 = eligible_mr[12:24]
    revenue_ttm = sum(m.get("revenue", 0) for m in last12) if len(last12) == 12 else None

    latest = eligible_mr[0] if eligible_mr else None
    latest_value = latest.get("revenue") if latest else None
    latest_label = f"{latest['revenue_year']}/{latest['revenue_month']:02d}" if latest else None

    # 當月 YoY
    revenue_yoy: Optional[float] = None
    if latest:
        ty = latest["revenue_year"] - 1
        tm = latest["revenue_month"]
        for m in eligible_mr:
            if m.get("revenue_year") == ty and m.get("revenue_month") == tm:
                if m.get("revenue"):
                    revenue_yoy = (latest_value - m["revenue"]) / m["revenue"] * 100
                break

    # TTM YoY
    revenue_ttm_yoy: Optional[float] = None
    if len(last12) == 12 and len(prev12) == 12:
        prev_sum = sum(m.get("revenue", 0) for m in prev12)
        if prev_sum:
            revenue_ttm_yoy = (revenue_ttm - prev_sum) / prev_sum * 100

    return {
        "found": True,
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "latest_month_label": latest_label,
        "latest_month_value": latest_value,
        "latest_month_yoy_pct": revenue_yoy,
        "ttm_value": revenue_ttm,
        "ttm_yoy_pct": revenue_ttm_yoy,
        "source": "FinMind v4 TaiwanStockMonthRevenue",
    }


# =====================================================================
# 5) /api/company/{stock_id}/dividend — FinMind 股利
# =====================================================================
@with_source_error_tracking
async def query_dividend(stock_id: str, as_of_str: Optional[str] = None) -> dict[str, Any]:
    stock_id = stock_id.strip()
    as_of = _resolve_as_of(as_of_str)
    start, end = _finmind_window(as_of)

    dv_rows = await get_dividend(stock_id, start, end)
    picked = _pick_dividend(dv_rows, as_of)
    return {
        "found": True,
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "dividend": picked,
        "source": "FinMind v4 TaiwanStockDividend",
    }


def _pick_dividend(rows: list[dict], as_of: date) -> Optional[dict]:
    candidates = []
    for r in rows:
        ex_cash = _parse_date(r.get("CashExDividendTradingDate") or "")
        ex_stock = _parse_date(r.get("StockExDividendTradingDate") or "")
        ann = _parse_date(r.get("date") or "")
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


# =====================================================================
# 6) /api/company/{stock_id}/value-chain — 櫃買中心 產業價值鏈
# =====================================================================
# 註：query_product_revenue 定義在本檔最末（見 7) 區塊）。


@with_source_error_tracking
async def query_value_chain(stock_id: str) -> dict[str, Any]:
    stock_id = stock_id.strip()
    # 觸發背景載入（首次）
    asyncio.create_task(icchain.ensure_loaded(background=True))

    if not icchain.is_loaded():
        return {
            "found": True,
            "stock_id": stock_id,
            "status": "loading" if icchain.is_loading() else "unavailable",
            "memberships": [],
            "neighbors_by_chain": {},
            "source": "櫃買中心 產業價值鏈資訊平台 ic.tpex.org.tw",
        }

    memberships = icchain.get_memberships(stock_id)
    neighbors: dict[str, dict] = {}
    seen_chain_codes = {m["ic_code"] for m in memberships}
    for ic_code in seen_chain_codes:
        chain = icchain.get_chain(ic_code) or {}
        if not chain:
            continue
        # 彈性分段：按官方 HTML 出現順序建立 streams，可容納「上中下游」以外的分段名稱
        # （例如5300 人工智慧的「應用與服務／核心技術／運算資源」）。
        streams_ordered: list[dict] = []
        # 同時保留舊欄位 upstream/midstream/downstream（向後相容）
        legacy_buckets: dict[str, list[dict]] = {"upstream": [], "midstream": [], "downstream": []}
        legacy_map = {"上游": "upstream", "中游": "midstream", "下游": "downstream"}
        for seg_zh, tops in (chain.get("segments") or {}).items():
            companies_in_seg: list[dict] = []
            for top in tops:
                for sub in top.get("sub_chains", []):
                    for c in sub.get("companies", []):
                        companies_in_seg.append({
                            "stk_code": c["stk_code"],
                            "name": c["name"],
                            "top_name": top["top_name"],
                            "sub_name": sub["sub_name"],
                            "is_self": c["stk_code"] == stock_id,
                        })
            # 去重（按 stk_code）
            seen = set()
            dedup: list[dict] = []
            for c in companies_in_seg:
                if c["stk_code"] in seen:
                    continue
                seen.add(c["stk_code"])
                dedup.append(c)
            streams_ordered.append({"segment": seg_zh, "companies": dedup})
            legacy_key = legacy_map.get(seg_zh)
            if legacy_key is not None:
                legacy_buckets[legacy_key] = dedup
        neighbors[ic_code] = {
            "ic_name": chain.get("ic_name", ""),
            "streams": streams_ordered,
            **legacy_buckets,
        }

    return {
        "found": True,
        "stock_id": stock_id,
        "status": "ready",
        "memberships": memberships,
        "neighbors_by_chain": neighbors,
        "source": "櫃買中心 產業價值鏈資訊平台 ic.tpex.org.tw",
    }


# =====================================================================
# 聚合 endpoint /api/company/{stock_id} — 完全等價於原本回應
# =====================================================================
@with_source_error_tracking
async def query(stock_id: str, as_of_str: Optional[str] = None) -> dict[str, Any]:
    stock_id = stock_id.strip()
    as_of = _resolve_as_of(as_of_str)

    # 先確認基本資料存在（決定是否提早 404）
    basic_raw = await _get_basic(stock_id)
    if not basic_raw:
        return {
            "found": False,
            "stock_id": stock_id,
            "as_of": as_of.isoformat(),
            "error": f"查無 {stock_id} 的上市櫃基本資料（可能為興櫃或已下市）",
        }

    # 平行呼叫 6 個獨立函式
    basic_r, bi_r, fin_r, rev_r, dv_r, vc_r = await asyncio.gather(
        query_basic(stock_id),
        query_business_items(stock_id),
        query_financials(stock_id, as_of_str),
        query_revenue(stock_id, as_of_str),
        query_dividend(stock_id, as_of_str),
        query_value_chain(stock_id),
    )

    return {
        "found": True,
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "basic": {
            "market": basic_r.get("market"),
            "company_name": basic_r.get("company_name"),
            "short_name": basic_r.get("short_name"),
            "english_name": basic_r.get("english_name"),
            "tax_id": basic_r.get("tax_id"),
            "paid_in_capital": basic_r.get("paid_in_capital"),
            "industry_code": basic_r.get("industry_code"),
            "industry_name": basic_r.get("industry_name"),
            "business_items": {
                "narrative": bi_r.get("narrative", []),
                "categories": bi_r.get("categories", []),
            },
            "general_manager": basic_r.get("general_manager"),
            "chairman": basic_r.get("chairman"),
            "incorporation_date": basic_r.get("incorporation_date"),
            "listing_date": basic_r.get("listing_date"),
            "website": basic_r.get("website"),
            "address": basic_r.get("address"),
        },
        "eps": fin_r["eps"],
        "revenue": {
            "latest_month_label": rev_r.get("latest_month_label"),
            "latest_month_value": rev_r.get("latest_month_value"),
            "latest_month_yoy_pct": rev_r.get("latest_month_yoy_pct"),
            "ttm_value": rev_r.get("ttm_value"),
            "ttm_yoy_pct": rev_r.get("ttm_yoy_pct"),
            "ttm_from_financial_statements": fin_r.get("revenue_ttm_from_financial_statements"),
        },
        "net_income": fin_r["net_income"],
        "operating_margin_pct": fin_r.get("operating_margin_pct"),
        "dividend": dv_r.get("dividend"),
        "sources": [
            "TWSE OpenAPI (上市公司基本資料)",
            "TPEx OpenAPI (上櫃公司基本資料)",
            "FinMind v4 (TaiwanStockFinancialStatements / MonthRevenue / Dividend)",
            "經濟部商工 (公司登記基本資料 - 所營事業)",
            "櫃買中心 產業價值鏈資訊平台 (上中下游定位)",
        ],
        "value_chain": {
            "status": vc_r.get("status"),
            "memberships": vc_r.get("memberships", []),
            "neighbors_by_chain": vc_r.get("neighbors_by_chain", {}),
        },
    }


# =====================================================================
# 搜尋
# =====================================================================
async def search_companies(keyword: str, limit: int = 20) -> list[dict]:
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


# =====================================================================
# 7) /api/company/{stock_id}/product-revenue — MOPS 主要產品比重
# =====================================================================
@with_source_error_tracking
async def query_product_revenue(stock_id: str, as_of_str: Optional[str] = None) -> dict[str, Any]:
    """查詢公司「各項產品業務營收統計表」（公開資訊觀測站 t05st08）。

    行為：
    - 當未提供 `as_of`：走「單一公司」流程（`ajax_t05st08`），
      回傳該公司「MOPS 上最後一次申報」期間之明細。
    - 當提供 `as_of`：走「彙總清單回溯」流程（`ajax_t05st08_all`），
      自 `as_of` 所在月份往回最多 24 個月，找出該公司本次起能找到的「最晚申報月份」之明細。
    """
    stock_id = stock_id.strip()
    as_of = _resolve_as_of(as_of_str)

    if as_of_str:
        # 走新流程：限定 as_of 之前（含當月）最晚申報期
        raw = await get_product_revenue_at(stock_id, as_of.year, as_of.month)
    else:
        raw = await get_product_revenue(stock_id)
    return {
        "found": bool(raw.get("found")),
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "year": raw.get("year"),
        "month": raw.get("month"),
        "company_name": raw.get("company_name"),
        "items": raw.get("items") or [],
        "sales_return": raw.get("sales_return"),
        "total_revenue": raw.get("total_revenue"),
        "notes": raw.get("notes"),
        "error": raw.get("error"),
        "source": "公開資訊觀測站 (MOPS) - 各項產品業務營收統計表 (t05st08)",
    }
