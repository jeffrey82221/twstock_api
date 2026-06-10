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
# MOPS 公開資訊觀測站「各項產品業務營收統計表 t05st08」
MOPS_REDIRECT_URL = "https://mops.twse.com.tw/mops/api/redirectToOld"
MOPS_T05ST08_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st08"

# ---- 快取 ----
# 基本資料：每天刷新一次
_basic_cache: TTLCache = TTLCache(maxsize=4, ttl=60 * 60 * 24)
# 財務數據：每小時刷新（FinMind 上限 300/hr）
_finmind_cache: TTLCache = TTLCache(maxsize=2048, ttl=60 * 60)
# 所營事業：每天刷新（資料每天更新一次）
_business_cache: TTLCache = TTLCache(maxsize=4096, ttl=60 * 60 * 24)
# MOPS 產品營收：6 小時（月報性質，不需頻繁重取）
_product_revenue_cache: TTLCache = TTLCache(maxsize=2048, ttl=60 * 60 * 6)
# MOPS (YM, TYPEK) → (set of co_ids, form_fields, step2_url)，6 小時；同月清單跨使用者共用
_mops_filer_cache: TTLCache = TTLCache(maxsize=4096, ttl=60 * 60 * 6)
# MOPS 指定期間（YM, stock_id）→ 產品營收 dict，6 小時
_product_revenue_at_cache: TTLCache = TTLCache(maxsize=4096, ttl=60 * 60 * 6)
# MOPS 該公司「最後申報期」(year, month) 或 None，24 小時；來自 t05st08（單一公司）快取加速回溯
_last_filed_cache: TTLCache = TTLCache(maxsize=4096, ttl=60 * 60 * 24)

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


# ---------- MOPS：各項產品業務營收統計表 t05st08 ----------
# 反爬說明：MOPS 新版 SPA 對舊版頁面採「兩段式」呼叫：
#   1) POST /mops/api/redirectToOld  →  回傳含加密 parameters 的 mopsov 舊版 URL
#   2) GET 該 URL  →  回 autoForm HTML，內含 yearmonth/year/month/TYPEK
#   3) POST mopsov ajax_t05st08（form-urlencoded，帶 step 2 參數）→ 拿到真正的 table HTML
# 必要 headers：Origin / Referer / User-Agent / Accept-Language，否則 WAF 會擋。
_MOPS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Origin": "https://mops.twse.com.tw",
    "Referer": "https://mops.twse.com.tw/mops/",
}


def _parse_mops_autoform(html: str) -> Optional[dict]:
    """從 step 1 回傳的 autoForm HTML 抽出 year/month/TYPEK/yearmonth。"""
    import re

    fields = {}
    for m in re.finditer(
        r"<input[^>]+name=['\"]([^'\"]+)['\"][^>]*value=['\"]([^'\"]*)['\"]",
        html,
        flags=re.IGNORECASE,
    ):
        fields[m.group(1)] = m.group(2)
    if "year" not in fields or "month" not in fields:
        return None
    return {
        "year": fields.get("year", ""),
        "month": fields.get("month", ""),
        "TYPEK": fields.get("TYPEK", ""),
        "yearmonth": fields.get("yearmonth", ""),
    }


def _parse_mops_table(html: str) -> dict:
    """從 step 2 回傳的 HTML 解析「各項產品業務營收」表。

    回傳：
      {
        "found": True/False,
        "year": "113", "month": "12",       # 民國年/月
        "company_name": "...",
        "items": [{"rank": "(1)", "name": "晶圓", "amount": 106545248_000}, ...],
        "sales_return": 4647193_000 or None,  # 銷貨退回及折讓
        "total_revenue": 117364912_000,       # 業務營收淨額（仟元換算為元）
        "notes": "..."
      }
    金額一律以「元」回傳（原始 HTML 為仟元，乘以 1000）。
    """
    import re

    result: dict = {
        "found": False,
        "year": None,
        "month": None,
        "company_name": None,
        "items": [],
        "sales_return": None,
        "total_revenue": None,
        "notes": None,
    }

    # 公司名稱
    m = re.search(r"<span style='color:blue;'>\(([^)]+)\)\s*([^<]+?)</span>", html)
    if m:
        result["company_name"] = m.group(2).strip()

    # 年月
    ym = re.search(
        r"民國</TD>\s*<TD>(\d+)</TD>\s*<TD>年</TD>\s*<TD>(\d+)</TD>\s*<TD>月</TD>",
        html,
        flags=re.IGNORECASE,
    )
    if ym:
        result["year"] = ym.group(1)
        result["month"] = ym.group(2).zfill(2)

    # 「無申報」/錯誤訊息
    if "該公司無申報" in html or "無此資料" in html:
        result["notes"] = "該公司無申報或該期間無資料。"
        return result

    # 找 class='hasBorder' 表
    tbl_m = re.search(
        r"<TABLE[^>]*class=['\"]hasBorder['\"][^>]*>(.*?)</TABLE>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not tbl_m:
        return result

    tbl_html = tbl_m.group(1)

    def _clean_amount(s: str) -> Optional[int]:
        s = re.sub(r"&nbsp;|\s|,", "", s)
        s = re.sub(r"<[^>]+>", "", s)
        if not s or s == "-":
            return None
        try:
            # 原 HTML 為「仟元」，轉換為元
            return int(float(s)) * 1000
        except (ValueError, TypeError):
            return None

    def _clean_text(s: str) -> str:
        s = re.sub(r"&nbsp;", " ", s)
        s = re.sub(r"<[^>]+>", "", s)
        return s.strip()

    # 解析每一列
    rows = re.findall(
        r"<TR[^>]*class=['\"](?:odd|even)['\"][^>]*>(.*?)</TR>",
        tbl_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for row in rows:
        cells = re.findall(r"<TD[^>]*>(.*?)</TD>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 3:
            continue
        c1 = _clean_text(cells[0])
        c2 = _clean_text(cells[1])
        c3_amount = _clean_amount(cells[2])

        # 「(1)」～「(10)」、「其他」為產品項目
        is_item = bool(re.match(r"^\(\d+\)$", c1)) or c1 == "其他"
        if is_item:
            if c2 and c3_amount is not None and c3_amount > 0:
                result["items"].append({
                    "rank": c1,
                    "name": c2,
                    "amount": c3_amount,
                })
            continue

        # 銷貨退回及折讓
        if c1 == "減" or "銷貨退回" in c2:
            result["sales_return"] = c3_amount
            continue

        # 業務營收淨額（合計）
        if c1 == "合計" or "業務營收淨額" in c2:
            result["total_revenue"] = c3_amount
            continue

    if result["items"] or result["total_revenue"]:
        result["found"] = True

    # 為每一項算佔比
    if result["found"] and result["total_revenue"]:
        denom = result["total_revenue"] + (result["sales_return"] or 0)
        for it in result["items"]:
            it["percentage"] = round(it["amount"] / denom * 100, 2) if denom else None

    return result


async def get_product_revenue(stock_id: str) -> dict:
    """查詢 MOPS 各項產品業務營收統計表（t05st08）。

    回傳結構：
      {
        "found": bool,
        "year": "113" | None,         # 民國年
        "month": "12" | None,         # 月份 (zero-padded)
        "company_name": str | None,
        "items": [{"rank": "(1)", "name": str, "amount": int, "percentage": float}],
        "sales_return": int | None,   # 銷貨退回及折讓（元）
        "total_revenue": int | None,  # 業務營收淨額（元）
        "notes": str | None,
        "error": str | None,
      }
    金額一律以「元」為單位（原 MOPS 為仟元已轉換）。
    """
    stock_id = (stock_id or "").strip()
    if not stock_id:
        return {"found": False, "error": "stock_id 不可為空"}
    if stock_id in _product_revenue_cache:
        return _product_revenue_cache[stock_id]

    out: dict = {
        "found": False,
        "year": None,
        "month": None,
        "company_name": None,
        "items": [],
        "sales_return": None,
        "total_revenue": None,
        "notes": None,
        "error": None,
    }

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=_MOPS_HEADERS,
        ) as client:
            # Step 1: POST redirectToOld 取得加密 URL
            redirect_payload = {
                "apiName": "ajax_t05st08",
                "parameters": {
                    "co_id": stock_id,
                    "isnew": "true",
                    "encodeURIComponent": 1,
                    "step": 1,
                    "firstin": 1,
                    "off": 1,
                },
            }
            r1 = await client.post(MOPS_REDIRECT_URL, json=redirect_payload)
            r1.raise_for_status()
            data1 = r1.json()
            if data1.get("code") != 200 or not data1.get("result", {}).get("url"):
                out["error"] = f"MOPS redirectToOld 失敗：{data1.get('message', 'unknown')}"
                _product_revenue_cache[stock_id] = out
                return out
            mopsov_url = data1["result"]["url"]

            # Step 2: GET mopsov 加密 URL → autoForm HTML
            r2 = await client.get(mopsov_url)
            r2.raise_for_status()
            r2.encoding = r2.encoding or "utf-8"
            html2 = r2.text

            # 該公司無申報的情況直接從 step 2 HTML 偵測
            if "該公司無申報" in html2:
                out["notes"] = "該公司未申報各項產品業務營收（採 IFRSs 後採自願申報）。"
                _product_revenue_cache[stock_id] = out
                return out

            form_fields = _parse_mops_autoform(html2)
            if not form_fields:
                # 也許 step 2 HTML 直接已經是 table（少數公司會這樣）
                parsed = _parse_mops_table(html2)
                if parsed["found"]:
                    out.update(parsed)
                else:
                    out["error"] = "MOPS step 2 未含 autoForm，也未含資料表。"
                _product_revenue_cache[stock_id] = out
                return out

            # Step 3: POST 完整參數取得 table HTML
            form_payload = {
                "encodeURIComponent": "1",
                "run": "",
                "steps": "1",
                "yearmonth": form_fields.get("yearmonth", ""),
                "colorchg": "",
                "TYPEK": form_fields.get("TYPEK", ""),
                "co_id": stock_id,
                "off": "1",
                "year": form_fields.get("year", ""),
                "month": form_fields.get("month", ""),
                "firstin": "true",
            }
            r3 = await client.post(
                MOPS_T05ST08_URL,
                data=form_payload,
                headers={
                    **_MOPS_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": MOPS_T05ST08_URL,
                    "Origin": "https://mopsov.twse.com.tw",
                },
            )
            r3.raise_for_status()
            r3.encoding = r3.encoding or "utf-8"
            html3 = r3.text
            parsed = _parse_mops_table(html3)
            if not parsed["found"]:
                # 若 step 3 沒成功，至少使用 step 2 的 year/month
                out["year"] = form_fields.get("year")
                out["month"] = form_fields.get("month", "").zfill(2) if form_fields.get("month") else None
                out["notes"] = parsed.get("notes") or "未解析到產品營收表內容。"
                _product_revenue_cache[stock_id] = out
                return out
            out.update(parsed)

    except httpx.HTTPError as e:
        out["error"] = f"MOPS 連線錯誤：{type(e).__name__}: {str(e)[:120]}"
    except Exception as e:
        out["error"] = f"MOPS 解析錯誤：{type(e).__name__}: {str(e)[:120]}"

    _product_revenue_cache[stock_id] = out
    return out


# =====================================================================
# MOPS 主要產品比重 — 指定日期回溯查詢
# =====================================================================
# 設計：MOPS 「各項產品業務營收統計表」採用 IFRSs 後改為自願申報，
#   主要 IFRS 上市公司（如 2330/2454/2603）只到某個歷史月份就停止申報。
#
# 三個 API 的角色：
#   1. ajax_t05st08（單一公司）：永遠回該公司「最後一次申報」的年月與明細，不接受年月參數。
#      → 透過這個可以一次拿到該公司的 last_filed_ym。
#   2. ajax_t05st08_all (YM, TYPEK)：可指定 YM=民國年月 5 碼，回該月該市場所有申報公司。
#   3. POST /mops/web/ajax_t05st08（帶 form-fm + co_id）：由上面該月拿該公司明細表。
#
# 加速回溯策略：
#   * 先查 last_filed_ym（stock_id 為 key 的 24h 快取）；
#     - 若 as_of ≥ last_filed_ym：直接跳到 last_filed_ym 拿明細—一次命中。
#     - 若 as_of < last_filed_ym：從 as_of 所在月份往回走，上限 last_filed_ym 之前任何月。
#   * 同月 (YM, TYPEK) 申報清單的快取兩層共用，不同使用者可重複命中。
MOPS_REDIRECT_URL_FOR_ALL = "https://mops.twse.com.tw/mops/api/redirectToOld"
_MAX_LOOKBACK_MONTHS_HARD = 240  # 硬上限防呆（MOPS 本表大約 2006 起，240 個月 = 20 年）


def _to_minguo_ym(year: int, month: int) -> str:
    """西元年月 → 民國年月 5 碼字串。例 2024,12 → '11312'。"""
    return f"{year - 1911}{month:02d}"


def _yyyy_mm_iter(start_year: int, start_month: int, max_steps: int):
    """從 (start_year, start_month) 開始往回走，yield 西元 (year, month)。"""
    y, m = start_year, start_month
    for _ in range(max_steps):
        yield y, m
        m -= 1
        if m == 0:
            m = 12
            y -= 1


async def _resolve_typek_for_stock(stock_id: str) -> list[str]:
    """根據 basic 資料推斷該公司 TYPEK；找不到時回傳兩者皆試。"""
    try:
        basic = await get_basic_data()
    except Exception:
        return ["sii", "otc"]
    item = basic.get(stock_id) if isinstance(basic, dict) else None
    market = (item or {}).get("market") if isinstance(item, dict) else None
    if market == "上市":
        return ["sii", "otc"]  # 主要 sii，fallback otc
    if market == "上櫃":
        return ["otc", "sii"]
    return ["sii", "otc"]


async def _fetch_filer_list(client: httpx.AsyncClient, ym: str, typek: str) -> tuple[set[str], Optional[dict], Optional[str]]:
    """取得 (YM, TYPEK) 的申報公司清單與 form-fm 欄位。

    回傳：(co_ids_set, form_fields, step2_url)
    若清單為空（該月該市場無人申報）或上游失敗，set 為空。
    form_fields 為 step2 HTML 內 <form name='fm'> 的所有 hidden input；
    step2_url 用於 step3 的 Referer。
    """
    cache_key = (ym, typek)
    cached = _mops_filer_cache.get(cache_key)
    if cached is not None:
        return cached[0], cached[1], cached[2]

    try:
        r1 = await client.post(
            MOPS_REDIRECT_URL_FOR_ALL,
            json={
                "apiName": "ajax_t05st08_all",
                "parameters": {
                    "YM": ym,
                    "TYPEK": typek,
                    "skind": "",
                    "encodeURIComponent": 1,
                    "step": 1,
                    "firstin": True,
                    "id": "",
                },
            },
        )
        r1.raise_for_status()
        data1 = r1.json()
        url2 = (data1.get("result") or {}).get("url") or data1.get("url")
        if not url2:
            _mops_filer_cache[cache_key] = (set(), None, None)
            return set(), None, None

        r2 = await client.get(url2)
        r2.raise_for_status()
        r2.encoding = r2.encoding or "utf-8"
        html = r2.text

        # 解析 form name='fm' 的 hidden inputs（step3 需要）
        # 重要：MOPS 該頁的 <form name='fm'> 並未閉合，hidden inputs 緊跟 form 開頭出現，
        # 因此不使用 `(.*?)</form>` 匹配，改抓 form open 之後的連續 hidden input。
        import re
        form_fields: dict[str, str] = {}
        form_open = re.search(
            r"<form[^>]*name=['\"]fm['\"][^>]*>",
            html,
            flags=re.IGNORECASE,
        )
        if form_open:
            head = html[form_open.end(): form_open.end() + 1500]
            for im in re.finditer(
                r"<input[^>]+name=[\"']([^\"']+)[\"'][^>]*value=[\"']([^\"']*)[\"']",
                head,
                flags=re.IGNORECASE,
            ):
                form_fields[im.group(1)] = im.group(2)

        # 解析該月該市場的所有公司代號
        # MOPS 清單頁每列「詳細資料」按鈕都帶有 onclick="co_id.value='1323';ajax1(...)"
        co_ids: set[str] = set()
        for m in re.finditer(
            r"co_id\.value\s*=\s*['\"](\d{3,6})['\"]",
            html,
            flags=re.IGNORECASE,
        ):
            co_ids.add(m.group(1))
        # 備援：表格 cell 中的純數字（4~6 碼）
        if not co_ids:
            for m in re.finditer(
                r"<td[^>]*>\s*(\d{4,6})\s*</td>",
                html,
                flags=re.IGNORECASE,
            ):
                co_ids.add(m.group(1))

        result = (co_ids, form_fields if form_fields else None, url2)
        _mops_filer_cache[cache_key] = result
        return result
    except Exception:
        _mops_filer_cache[cache_key] = (set(), None, None)
        return set(), None, None


async def _fetch_detail_at(client: httpx.AsyncClient, stock_id: str, form_fields: dict, referer: str) -> dict:
    """以 step3 POST 取得某月該公司之產品營收明細，回傳 _parse_mops_table() 結果。"""
    payload = dict(form_fields)  # 包含 colorchg/yearmonth/year/month/TYPEK/off/steps/firstin
    payload["co_id"] = stock_id
    # 確保有 encodeURIComponent
    payload.setdefault("encodeURIComponent", "1")

    r = await client.post(
        MOPS_T05ST08_URL,
        data=payload,
        headers={
            **_MOPS_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": referer,
            "Origin": "https://mopsov.twse.com.tw",
        },
    )
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    parsed = _parse_mops_table(r.text)
    return parsed


async def _get_last_filed_ym(stock_id: str) -> Optional[tuple[int, int]]:
    """利用 t05st08（單一公司）取得該公司「最後申報期」的 (西元年, 月)。

    - 主要作用為「加速」：縮短回溯起點。
    - 以 stock_id 為 key 快取 24h（這個值極為穩定—IFRS 上市公司離現在已停止申報多年）。
    - 若該公司從未申報或上游失效，回 None。
    """
    if stock_id in _last_filed_cache:
        return _last_filed_cache[stock_id]

    raw = await get_product_revenue(stock_id)
    last: Optional[tuple[int, int]] = None
    if raw.get("found") and raw.get("year") and raw.get("month"):
        try:
            mg_year = int(raw["year"])
            m = int(raw["month"])
            last = (mg_year + 1911, m)
        except (ValueError, TypeError):
            last = None
    _last_filed_cache[stock_id] = last
    return last


async def get_product_revenue_at(stock_id: str, as_of_year: int, as_of_month: int) -> dict:
    """查詢「指定日期之前的最晚申報期」之主要產品比重。

    參數：
      - stock_id：股票代號
      - as_of_year/as_of_month：西元年月（含當月，由此往回找）

    加速策略：
      1. 先查 last_filed_ym（來自 t05st08、stock_id 為 key 的 24h 快取）。
      2. 若 as_of ≥ last_filed_ym：起點跳到 last_filed_ym，首輪即命中。
      3. 若 as_of < last_filed_ym：從 as_of 往回走；由於 IFRS 後多為年底一次申報，
         可能需數個月才命中上一個年度申報之月。
      4. 若取不到 last_filed_ym，退為全面回溯至硬上限 (240 個月)。

    回傳結構與 `get_product_revenue()` 相同（year/month 為民國年）。
    """
    stock_id = (stock_id or "").strip()
    if not stock_id:
        return {"found": False, "error": "stock_id 不可為空"}

    # 快取 key：以「該月 YM」為粒度（同公司同月之結果穩定）
    start_ym = _to_minguo_ym(as_of_year, as_of_month)
    cache_key = f"{stock_id}@{start_ym}"
    if cache_key in _product_revenue_at_cache:
        return _product_revenue_at_cache[cache_key]

    out: dict = {
        "found": False,
        "year": None,
        "month": None,
        "company_name": None,
        "items": [],
        "sales_return": None,
        "total_revenue": None,
        "notes": None,
        "error": None,
    }

    # 步驟 1：取 last_filed_ym（快取加速）
    last_filed = await _get_last_filed_ym(stock_id)

    # 步驟 2：決定回溯起點與上限步數
    as_of_t = (as_of_year, as_of_month)
    if last_filed is None:
        # t05st08 未回傳該公司 — 代表 MOPS 根本沒有此公司任何申報記錄，直接短路。
        out["notes"] = (
            "公開資訊觀測站查無該公司「各項產品業務營收統計表」任何申報記錄。"
            "可能原因：代號不存在、公司未上市柜、或採用 IFRSs 後未曾自願揭露。"
        )
        _product_revenue_at_cache[cache_key] = out
        return out

    if last_filed <= as_of_t:
        # as_of ≥ last_filed—直接從 last_filed 出發（首輪命中）
        start_y, start_m = last_filed
        max_steps = 6  # 少量預留，防某些月 TYPEK 清單上游未回
        lower_bound = last_filed
    else:
        # as_of < last_filed—從 as_of 往回找。由於 last_filed 看得見，
        # 最多只需走到「該公司出現的趨近」之月，其間仍可能不連續申報，
        # 所以仍使用硬上限，但該公司「必定」能被找到（因為 last_filed 存在）。
        start_y, start_m = as_of_year, as_of_month
        max_steps = _MAX_LOOKBACK_MONTHS_HARD
        lower_bound = None

    typek_order = await _resolve_typek_for_stock(stock_id)

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=_MOPS_HEADERS,
        ) as client:
            found_at: Optional[tuple[str, str, dict, str]] = None
            for y, m in _yyyy_mm_iter(start_y, start_m, max_steps):
                # 提前終止：走到 last_filed 之前仍未命中 → 出來
                if lower_bound is not None and (y, m) < lower_bound:
                    break
                ym = _to_minguo_ym(y, m)
                for typek in typek_order:
                    co_ids, form_fields, step2_url = await _fetch_filer_list(client, ym, typek)
                    if not co_ids or not form_fields:
                        continue
                    if stock_id in co_ids:
                        found_at = (ym, typek, form_fields, step2_url or MOPS_T05ST08_URL)
                        break
                if found_at:
                    break

            if not found_at:
                if last_filed is not None:
                    lf_mg = last_filed[0] - 1911
                    out["notes"] = (
                        f"該公司最後申報期為民國 {lf_mg}/{last_filed[1]:02d}"
                        f"（西元 {last_filed[0]}/{last_filed[1]:02d}），晚於指定日期。"
                        "「在該日期之前」查無該公司的申報記錄。"
                    )
                else:
                    out["notes"] = (
                        f"自 {as_of_year}/{as_of_month:02d} 起回溯 {max_steps} 個月，"
                        "未在公開資訊觀測站找到該公司之「各項產品業務營收統計表」申報記錄。"
                        "採用 IFRSs 後此表為自願申報，部分公司已停止揭露。"
                    )
                _product_revenue_at_cache[cache_key] = out
                return out

            ym, typek, form_fields, step2_url = found_at
            parsed = await _fetch_detail_at(client, stock_id, form_fields, step2_url)
            if not parsed.get("found"):
                out["year"] = form_fields.get("year")
                month_v = form_fields.get("month", "")
                out["month"] = month_v.zfill(2) if month_v else None
                out["notes"] = parsed.get("notes") or (
                    f"找到該公司於民國 {form_fields.get('year')}/{month_v} 申報，但解析明細表失敗。"
                )
                _product_revenue_at_cache[cache_key] = out
                return out

            out.update(parsed)
    except httpx.HTTPError as e:
        out["error"] = f"MOPS 連線錯誤：{type(e).__name__}: {str(e)[:120]}"
    except Exception as e:
        out["error"] = f"MOPS 處理錯誤：{type(e).__name__}: {str(e)[:120]}"

    _product_revenue_at_cache[cache_key] = out
    return out
