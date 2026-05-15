"""免費公開資料來源 client。

來源：
- TWSE OpenAPI: 上市公司基本資料 t187ap03_L
- TPEx OpenAPI: 上櫃公司基本資料 mopsfin_t187ap03_O
- FinMind v4: 月營收、季財報、股利政策（免費 300 req/hr，無需 token）
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
from cachetools import TTLCache

# ---- 端點 ----
TWSE_BASIC_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_BASIC_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
# 經濟部商工 - 公司登記基本資料(應用三)，含「所營事業 Cmp_Business」
GCIS_BUSINESS_URL = "https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C"

# ---- 快取 ----
# 基本資料：每天刷新一次
_basic_cache: TTLCache = TTLCache(maxsize=4, ttl=60 * 60 * 24)
# 財務數據：每小時刷新（FinMind 上限 300/hr）
_finmind_cache: TTLCache = TTLCache(maxsize=2048, ttl=60 * 60)
# 所營事業：每天刷新（資料每天更新一次）
_business_cache: TTLCache = TTLCache(maxsize=4096, ttl=60 * 60 * 24)

_basic_lock = asyncio.Lock()


async def _http_get(url: str, params: Optional[dict] = None, timeout: float = 30.0, retries: int = 3) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                await asyncio.sleep(0.6 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


# ---------- 基本資料 ----------
async def load_basic_table() -> dict[str, dict]:
    """回傳 stock_id -> 基本資料 dict（標準化欄位）。
    合併上市 + 上櫃，欄位名統一為中文 key。
    """
    if "merged" in _basic_cache:
        return _basic_cache["merged"]

    async with _basic_lock:
        if "merged" in _basic_cache:
            return _basic_cache["merged"]

        # 序列拉避免帶註並發被 TWSE 拒絕
        try:
            twse_data = await _http_get(TWSE_BASIC_URL)
        except Exception as e:
            twse_data = e
        try:
            tpex_data = await _http_get(TPEX_BASIC_URL)
        except Exception as e:
            tpex_data = e

        merged: dict[str, dict] = {}

        if not isinstance(twse_data, Exception):
            for row in twse_data:
                code = (row.get("公司代號") or "").strip()
                if not code:
                    continue
                merged[code] = {
                    "market": "上市",
                    "stock_id": code,
                    "company_name": (row.get("公司名稱") or "").strip(),
                    "short_name": (row.get("公司簡稱") or "").strip(),
                    "industry_code": (row.get("產業別") or "").strip(),
                    "tax_id": (row.get("營利事業統一編號") or "").strip(),
                    "chairman": (row.get("董事長") or "").strip(),
                    "general_manager": (row.get("總經理") or "").strip(),
                    "paid_in_capital": _to_int(row.get("實收資本額")),
                    "incorporation_date": (row.get("成立日期") or "").strip(),
                    "listing_date": (row.get("上市日期") or "").strip(),
                    "address": (row.get("住址") or "").strip(),
                    "website": (row.get("網址") or "").strip(),
                    "english_name": (row.get("英文簡稱") or "").strip(),
                }

        if not isinstance(tpex_data, Exception):
            for row in tpex_data:
                code = (row.get("SecuritiesCompanyCode") or "").strip()
                if not code:
                    continue
                merged[code] = {
                    "market": "上櫃",
                    "stock_id": code,
                    "company_name": (row.get("CompanyName") or "").strip(),
                    "short_name": (row.get("CompanyAbbreviation") or "").strip(),
                    "industry_code": (row.get("SecuritiesIndustryCode") or "").strip(),
                    "tax_id": (row.get("UnifiedBusinessNo.") or "").strip(),
                    "chairman": (row.get("Chairman") or "").strip(),
                    "general_manager": (row.get("GeneralManager") or "").strip(),
                    "paid_in_capital": _to_int(row.get("Paidin.Capital.NTDollars")),
                    "incorporation_date": (row.get("DateOfIncorporation") or "").strip(),
                    "listing_date": (row.get("DateOfListing") or "").strip(),
                    "address": (row.get("Address") or "").strip(),
                    "website": (row.get("WebAddress") or "").strip(),
                    "english_name": (row.get("Symbol") or "").strip(),
                }

        if merged:
            _basic_cache["merged"] = merged
        return merged


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "－"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


# ---------- FinMind ----------
async def finmind_fetch(dataset: str, stock_id: str, start_date: str, end_date: str) -> list[dict]:
    """查 FinMind 任一 dataset。回傳 list of rows。"""
    cache_key = (dataset, stock_id, start_date, end_date)
    if cache_key in _finmind_cache:
        return _finmind_cache[cache_key]

    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        data = await _http_get(FINMIND_URL, params=params, timeout=30.0)
    except Exception:
        return []

    if not isinstance(data, dict) or data.get("msg") != "success":
        rows: list[dict] = []
    else:
        rows = data.get("data") or []

    _finmind_cache[cache_key] = rows
    return rows


async def get_month_revenue(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    return await finmind_fetch("TaiwanStockMonthRevenue", stock_id, start_date, end_date)


async def get_financial_statements(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    return await finmind_fetch("TaiwanStockFinancialStatements", stock_id, start_date, end_date)


async def get_dividend(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    return await finmind_fetch("TaiwanStockDividend", stock_id, start_date, end_date)


# ---------- 商工：所營事業 ----------
async def get_business_scope(tax_id: str) -> list[dict]:
    """以營利事業統一編號查詢商工 API，回傳 Cmp_Business 列表。

    每個 item 結構：
      - Business_Seq_NO: 序號
      - Business_Item: 行業代碼（純空白字串代表為「敘述性條目」）
      - Business_Item_Desc: 描述文字
    """
    tax_id = (tax_id or "").strip()
    if not tax_id:
        return []
    if tax_id in _business_cache:
        return _business_cache[tax_id]

    # 商工 API 必須以原樣 query string 傳遞（$ 須編碼為 %24）
    url = (
        f"{GCIS_BUSINESS_URL}"
        f"?%24format=json&%24filter=Business_Accounting_NO%20eq%20{tax_id}"
    )
    try:
        data = await _http_get(url, timeout=20.0, retries=2)
    except Exception:
        return []

    if not isinstance(data, list) or not data:
        _business_cache[tax_id] = []
        return []

    items = data[0].get("Cmp_Business") or []
    out: list[dict] = []
    for it in items:
        code = (it.get("Business_Item") or "").strip()
        desc = (it.get("Business_Item_Desc") or "").strip()
        if not desc:
            continue
        # 去除前綴序號標點，例如「１．」「2.」
        clean_desc = desc.lstrip("０１２３４５６７８９0123456789．. 、").strip()
        out.append({
            "code": code,  # 空字串代表為敘述條目
            "desc": clean_desc or desc,
            "is_narrative": code == "",
        })
    _business_cache[tax_id] = out
    return out
