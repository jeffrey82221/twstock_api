"""FastAPI 入口。

本檔同時負責產出 Swagger / OpenAPI 文件 — 每個 endpoint 都標註：
1. 上游資料源網站正式名稱
2. 上游 API URL 與呼叫方式（input / output）
3. 上游回應如何被處理成本 endpoint 的輸出
"""
from __future__ import annotations

import asyncio
import logging

from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query

logger = logging.getLogger("twstock_api.main")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import icchain, ohlcv_source
from .schemas import (
    BusinessItemsResponse,
    ChainResponse,
    ChainsListResponse,
    CompanyBasicResponse,
    CompanyResponse,
    CompanyValueChainResponse,
    DividendHistoryResponse,
    DividendResponse,
    FinancialsResponse,
    HealthResponse,
    OhlcvResponse,
    ProductRevenueFilersResponse,
    ProductRevenueResponse,
    RevenueResponse,
    SearchResponse,
)
from .service import (
    query,
    query_basic,
    query_business_items,
    query_dividend,
    query_dividend_history,
    query_dividend_history_yfinance,
    query_dividend_yfinance,
    query_revenue_twse,
    query_financials,
    query_financials_yfinance,
    query_product_revenue,
    query_product_revenue_filers,
    query_revenue,
    query_value_chain,
    search_companies,
)

# ---- OpenAPI 描述 ----
API_DESCRIPTION = """
**TWStock Query** — 整合多個免費公開資料源，提供任一台股上市/上櫃公司的基本資料、
財報衍生 KPI（EPS、營收、淨利、營業利潤率、營收成長率、股利）與產業價值鏈定位。

## 上游資料源

| 來源 | 正式名稱 | URL |
| --- | --- | --- |
| **TWSE** | 證券交易所 OpenAPI — 上市公司基本資料 t187ap03_L | <https://openapi.twse.com.tw/v1/opendata/t187ap03_L> |
| **TPEx** | 櫃買中心 OpenAPI — 上櫃公司基本資料 mopsfin_t187ap03_O | <https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O> |
| **FinMind** | FinMind v4 開放金融資料庫 | <https://api.finmindtrade.com/api/v4/data> |
| **GCIS** | 經濟部商工登記公示資料 — 公司登記基本資料 | <https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C> |
| **TPEx IC** | 櫃買中心 產業價值鏈資訊平台 | <https://ic.tpex.org.tw/> |

## 共通設計
- `as_of` 可指定任一日期回推 TTM/年化值（不傳則用今天）。
- 所有上游皆免費、無需 token。
- 各上游皆在記憶體有 TTL 快取（基本資料 24h、FinMind 1h、商工 24h、產業價值鏈 7 天）。
- 每個 endpoint 回應的 Pydantic 欄位 `description` 都記載對應上游欄位與處理邏輯，請直接在
  「Schema」分頁展開欄位細節。
"""

app = FastAPI(
    title="台灣上市櫃公司查詢 API",
    description=API_DESCRIPTION,
    version="1.1.0",
    contact={
        "name": "TWStock Query",
        "url": "https://ic.tpex.org.tw/",
    },
    openapi_tags=[
        {"name": "Company (aggregated)", "description": "聚合 endpoint：一次拿齊六項資訊"},
        {"name": "Company (per-source)", "description": "按資料源拆分的 6 個獨立 endpoint"},
        {"name": "Market Data", "description": "日 OHLCV 行情（TWSE + TPEx 融合，智能切換 per-day / per-stock-per-month 策略）"},
        {"name": "Search", "description": "公司搜尋"},
        {"name": "Value Chain", "description": "產業價值鏈（櫃買中心 ic.tpex.org.tw）"},
        {"name": "System", "description": "健康檢查"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
async def _on_startup() -> None:
    # 啟動時依磁碟快取快速還原；若無快取則背景抓取。
    await icchain.ensure_loaded(background=True)


# =====================================================================
# System
# =====================================================================
@app.get(
    "/api/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="服務健康檢查 + 產業價值鏈載入狀態",
    description=(
        "回傳本服務是否可用，以及 `ic.tpex.org.tw` 47 條產業鏈背景載入進度。\n\n"
        "**處理流程**：\n"
        "1. 不呼叫任何外部 API，只回讀程序內部狀態。\n"
        "2. `icchain.status()` 來自 `app/icchain.py` 的全域 dict（記錄 loaded / loading / chains / companies / age_seconds）。"
    ),
)
async def health():
    return {"ok": True, "icchain": icchain.status()}


# =====================================================================
# Value Chain
# =====================================================================
@app.get(
    "/api/chains",
    response_model=ChainsListResponse,
    tags=["Value Chain"],
    summary="列出全部 47 條產業鏈",
    description=(
        "**資料源**：櫃買中心 · 產業價值鏈資訊平台 <https://ic.tpex.org.tw/>。\n\n"
        "**上游 API**：無 JSON API。本清單來自 `app/icchain.py::IC_CHAINS` 常數（依研究報告整理自首頁下拉選單）。\n\n"
        "**處理流程**：\n"
        "1. 直接回傳 `IC_CHAINS` 內的 `(ic_code, ic_name)` 列表。\n"
        "2. 同時附帶 `icchain.status()` 讓前端判斷下一步可否呼叫 `/api/chain/{ic_code}`。"
    ),
)
async def api_list_chains():
    return {"chains": icchain.list_chains(), "status": icchain.status()}


@app.get(
    "/api/chain/{ic_code}",
    response_model=ChainResponse,
    tags=["Value Chain"],
    summary="取得某條產業鏈的完整上中下游樹",
    description=(
        "**資料源**：櫃買中心 · 產業價值鏈資訊平台。\n\n"
        "**上游 API**：`GET https://ic.tpex.org.tw/introduce.php?ic={IC_CODE}`（server-rendered HTML，無 JSON）。\n"
        "- Input：路徑參數 `ic_code`（例如 `D000` 半導體、`A300` 電動車、`5300` 人工智慧）。\n"
        "- Output：HTML 頁面（~290 KB），用 BeautifulSoup(lxml) 解析。\n\n"
        "**處理流程**：\n"
        "1. 首次任一查詢觸發 `ensure_loaded(background=True)`：以 `asyncio.Semaphore(6)` 並行下載 47 條鏈頁面（~8 秒）。\n"
        "2. 對每頁解析 `main_ic_panel` 內每個 `<div class=\"chain\">`：\n"
        "   - 從 `chain-title-panel` 文字判定段位（上游/中游/下游）；\n"
        "   - 列出該段位的 `ic_link_{top_code}` 節點（下層分類）；\n"
        "   - 對每個下層分類，讀 `sc-ind-pnl_{top_code}` 內的 `sc_link_{sub_code}` 與 `sc_company_{sub_code}` 表格；\n"
        "   - 若該下層分類沒 sub-chain，退回 `companyList_{top_code}` 直接列出公司；\n"
        "   - HTML 內若有 `<b>外國</b>` 標題段，整段略過，只保留 `<b>本國</b>`。\n"
        "3. 結果寫入 `data/icchain.json`（~1-2 MB，TTL 7 天）；下次重啟由磁碟快取直接還原。\n"
        "4. 收到本 endpoint 請求時，從記憶體 `chain_tree[ic_code]` 序列化回傳。\n\n"
        "**錯誤**：載入未完成回 503；查無代碼回 404。"
    ),
    responses={
        404: {"description": "查無此產業鏈代碼"},
        503: {"description": "47 條鏈背景載入中，請稍後重試"},
    },
)
async def api_chain(ic_code: str):
    await icchain.ensure_loaded(background=True)
    if not icchain.is_loaded():
        raise HTTPException(status_code=503, detail="產業價值鏈資料載入中，請稍後重試")

    # 快取回應：已有序列化的 JSON bytes 就直接回，跳過 pydantic
    # validation 與 FastAPI json encode。cache invalidate 由
    # icchain._fetch_all / _load_from_disk 兩個寫入點負責，這裡需要
    # 校正 uppercase，避免大小寫不同顯入 miss/hit 不一致。
    key = ic_code.upper()
    cached = icchain.get_chain_response_bytes(key)
    if cached is not None:
        return Response(content=cached, media_type="application/json")

    chain = icchain.get_chain(ic_code)
    if not chain:
        raise HTTPException(status_code=404, detail=f"查無產業鏈 {ic_code}")

    # 守門：若這條鏈回來的完全沒公司（上次 fetch 對它憑空、但其他鏈
    # 還健康，以致 index-level 毒 cache 防線沒觸發），就在背景冒一次強
    # 制重抓，下次同一個 ic_code 的查詢就能拿到公司。這里不阻塞本
    # 次回應，避免將單一 endpoint 的延遲拉到全量抓取的時間。
    company_count = icchain._chain_company_count(chain)
    if company_count == 0 and not icchain.is_loading():
        logger.warning(
            "[api_chain] ic_code=%s has 0 companies in cached tree; "
            "triggering background force refresh", ic_code,
        )
        asyncio.create_task(icchain.ensure_loaded(background=False, force=True))
        # 公司零的回應不 cache：下次 refetch 回來公司已填好，也會作廢
        # 整區 cache。這邊直接回 dict。
        return chain

    # 以 pydantic model validate + 序列化一次，存進 cache。沒進 cache 前
    # 先 validate，若 malformed 新規 data 會在這裡就 500 而不是把壞 bytes
    # 給下次 request。
    payload = ChainResponse.model_validate(chain).model_dump_json().encode("utf-8")
    icchain.set_chain_response_bytes(key, payload)
    return Response(content=payload, media_type="application/json")


@app.post(
    "/api/chain/refresh",
    tags=["Value Chain"],
    summary="強制重抓 47 條產業鏈（恢復毒 cache 用）",
    description=(
        "忽略記憶體與磁碟 `data/icchain.json` cache，重新向櫃買網站抓 47 條鏈頁面。\n\n"
        "使用時機：\n"
        "1. `/api/chain/{ic_code}` 回 200 但 `segments` 內 companies 全部空（毒 cache 症狀）。\n"
        "2. `/api/status` 看到 `icchain.health_ratio` 過低。\n"
        "3. 手動強制刷新這一现已過 TTL 的資料。\n\n"
        "行為：阻塞直到重抓完成（約 8 秒），這樣呼叫者一回就知道重抓結果（項目數、 "
        "公司數、毒 cache 防線判定結果）。"
    ),
)
async def api_chain_refresh():
    await icchain.ensure_loaded(background=False, force=True)
    return {"ok": True, "status": icchain.status()}


# =====================================================================
# Company
# =====================================================================
@app.get(
    "/api/search",
    response_model=SearchResponse,
    tags=["Search"],
    summary="模糊搜尋公司（代號 / 中文名 / 簡稱 / 英文簡稱）",
    description=(
        "**資料源**：\n"
        "- 證券交易所 (TWSE) OpenAPI · 上市公司基本資料 t187ap03_L\n"
        "- 櫃買中心 (TPEx) OpenAPI · 上櫃公司基本資料 mopsfin_t187ap03_O\n\n"
        "**上游 API**：\n"
        "- `GET https://openapi.twse.com.tw/v1/opendata/t187ap03_L`（無參數，回 JSON Array）\n"
        "- `GET https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`（無參數，回 JSON Array）\n\n"
        "**處理流程**：\n"
        "1. 程序啟動或快取（24h TTL）過期時，序列呼叫上述兩個端點（避免併發被擋）。\n"
        "2. 把 TWSE 的中文 key 與 TPEx 的英文 key 統一映射成內部標準 dict（`stock_id` → 公司資料），合併存於記憶體。\n"
        "3. 對輸入 `q` 在 `stock_id` / `公司全名` / `簡稱` / `英文簡稱` 做大小寫不敏感子字串比對。\n"
        "4. 取最多 `limit` 筆，附上 `industry_name`（由 `industry_code` 透過 `app/industry.py` 對照表轉中文）。"
    ),
)
async def api_search(
    q: str = Query(..., description="關鍵字：股票代號 / 公司全名 / 簡稱 / 英文簡稱"),
    limit: int = Query(20, description="最多回傳幾筆，預設 20", ge=1, le=200),
):
    return {"results": await search_companies(q, limit=limit)}


@app.get(
    "/api/company/{stock_id}",
    response_model=CompanyResponse,
    tags=["Company (aggregated)"],
    summary="一次查詢一家公司的六項資訊（聚合 endpoint）",
    description=(
        "本 endpoint 為「聚合 endpoint」，內部平行呼叫以下 6 個獨立 endpoint 後組合回傳：\n"
        "| 資料區塊 | 獨立 endpoint |\n"
        "| --- | --- |\n"
        "| `basic.*`（除 business_items） | `GET /api/company/{stock_id}/basic` |\n"
        "| `basic.business_items` | `GET /api/company/{stock_id}/business-items` |\n"
        "| `eps` / `net_income` / `operating_margin_pct` | `GET /api/company/{stock_id}/financials` |\n"
        "| `revenue` | `GET /api/company/{stock_id}/revenue` |\n"
        "| `dividend` | `GET /api/company/{stock_id}/dividend` |\n"
        "| `value_chain` | `GET /api/company/{stock_id}/value-chain` |\n\n"
        "需要微服務式各自拉資料、或快取粒度更細的使用者請改用上表列出的 6 個 endpoint。以下是各區塊與上游資料源的對應說明：\n\n"
        "整合 5 個免費公開資料源，回傳一份綜合資料。各區塊上游與處理規則：\n\n"
        "### 1. `basic.*` — 公司基本資料\n"
        "- 來源：**TWSE OpenAPI t187ap03_L** 與 **TPEx OpenAPI mopsfin_t187ap03_O**。\n"
        "- 呼叫：兩個都是 `GET`、無參數、無需 token，回傳整份上市/上櫃公司清單。\n"
        "- 處理：合併兩份清單，把 TWSE 中文 key（`公司代號`、`公司名稱`、`實收資本額`、`總經理` …）與\n"
        "  TPEx 英文 key（`SecuritiesCompanyCode`、`CompanyName`、`Paidin.Capital.NTDollars` …）映射為內部統一欄位；\n"
        "  日期欄位若為 7 位民國年（`1150508`）轉成 `YYYY-MM-DD`。\n\n"
        "### 2. `basic.business_items` — 主要營業項目\n"
        "- 來源：**經濟部商工登記公示資料 · 公司登記基本資料**（資料集 `236EE382-...025E7C`）。\n"
        "- 呼叫：`GET https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C"
        "?$format=json&$filter=Business_Accounting_NO eq {TAX_ID}`（OData filter，`$` 須 URL-encode 為 `%24`）。\n"
        "- 處理：取回傳 `[0].Cmp_Business` 陣列；`Business_Item` 為空字串者視為敘述條目（最具識別度），\n"
        "  其餘為行業分類代碼；`Business_Item_Desc` 為描述文字，會去除前綴序號（「１．」「2.」等）。\n\n"
        "### 3. `eps` / `net_income` / `operating_margin_pct` — 季財報衍生\n"
        "- 來源：**FinMind v4** dataset `TaiwanStockFinancialStatements`。\n"
        "- 呼叫：`GET https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements"
        "&data_id={stock_id}&start_date={as_of-5y}&end_date={as_of}`，回 `{msg, data: [{date, type, value}]}`。\n"
        "- 處理：把 `data` rows 轉成 `{date: {type: value}}` map；對 `EPS` / `IncomeAfterTaxes` / `OperatingIncome` / `Revenue`\n"
        "  分別取「`date ≤ as_of` 的最近 4 季 value 加總」即為各自的 TTM；不足 4 季回 null。\n"
        "  `operating_margin_pct = OperatingIncome(TTM) / Revenue(TTM) × 100`。\n\n"
        "### 4. `revenue` — 月營收 + YoY\n"
        "- 來源：**FinMind v4** dataset `TaiwanStockMonthRevenue`。\n"
        "- 呼叫：與上同，僅 dataset 不同，回傳每月 `{date, revenue_year, revenue_month, revenue}`。\n"
        "- 處理：\n"
        "  - `latest_month_*`：取 `date ≤ as_of` 排序後最新一筆。\n"
        "  - `latest_month_yoy_pct`：找 `revenue_year-1` 同月，公式 `(本月-去年同月)/去年同月 × 100`。\n"
        "  - `ttm_value`：最近 12 個月加總；不足 12 筆回 null。\n"
        "  - `ttm_yoy_pct`：最近 12 月加總 vs. 再往前 12 月加總。\n\n"
        "### 5. `dividend` — 股利\n"
        "- 來源：**FinMind v4** dataset `TaiwanStockDividend`。\n"
        "- 處理：取「除息日 ≤ as_of」最後一次股利；現金 = `CashEarningsDistribution + CashStatutorySurplus`，\n"
        "  股票 = `StockEarningsDistribution + StockStatutorySurplus`。\n\n"
        "### 6. `value_chain` — 產業價值鏈定位\n"
        "- 來源：**櫃買中心 · 產業價值鏈資訊平台** <https://ic.tpex.org.tw/introduce.php?ic={IC_CODE}>。\n"
        "- 呼叫：47 條鏈頁面（server-rendered HTML），首次查詢觸發背景全量收集，落盤 `data/icchain.json`（TTL 7 天）。\n"
        "- 處理：以 BeautifulSoup(lxml) 解析 `main_ic_panel` / `sc-ind-pnl_*` / `sc_company_*` 結構；\n"
        "  以 `stk_code` 反查 `company_index`（嚴謹，不做模糊比對），輸出 `memberships`；\n"
        "  並從 `chain_tree[ic_code]` 列出同鏈所有上/中/下游公司於 `neighbors_by_chain`。"
    ),
    responses={404: {"description": "查無此股票代號的上市櫃基本資料（可能為興櫃或已下市）"}},
)
async def api_company(
    stock_id: str,
    as_of: str | None = Query(
        None,
        description=(
            "查詢基準日 `YYYY-MM-DD`；省略則用今天。回傳的 TTM/月營收皆以此日往前回推。"
            "例如查詢台積電 2330，傳 `as_of=2024-12-31` 會回到 2024 年底為止的 KPI。"
        ),
    ),
):
    result = await query(stock_id, as_of)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result.get("error", "查無資料"))
    return result


# =====================================================================
# 6 個拆分後的獨立 endpoint
# =====================================================================
_AS_OF_DESC = (
    "查詢基準日 `YYYY-MM-DD`；省略則用今天。回傳的 TTM/月營收皆以此日往前回推。"
)


@app.get(
    "/api/company/{stock_id}/basic",
    response_model=CompanyBasicResponse,
    tags=["Company (per-source)"],
    summary="公司基本資料（TWSE / TPEx OpenAPI）",
    description=(
        "**資料來源網站正式名稱**：證券交易所 (TWSE) OpenAPI、櫃買中心 (TPEx) OpenAPI。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `GET https://openapi.twse.com.tw/v1/opendata/t187ap03_L`（上市）\n"
        "- `GET https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`（上櫃）\n"
        "- Input：無參數、無 token。\n"
        "- Output：JSON Array，每筆是一家公司的資料 dict。\n\n"
        "**處理邏輯**：\n"
        "1. 並行拉上述兩個端點並合併（24h TTL 快取）為 `stock_id -> 公司資料` dict。\n"
        "2. 本 endpoint 隨 stock_id 查表。\n"
        "3. 中/英文 key 統一到內部標準欄位；民國年日期轉 `YYYY-MM-DD`；`industry_code` 透過 `app/industry.py` 轉中文。\n\n"
        "查無公司時：200 + `found=false` + `error`。"
    ),
)
async def api_company_basic(stock_id: str):
    return await query_basic(stock_id)


@app.get(
    "/api/company/{stock_id}/business-items",
    response_model=BusinessItemsResponse,
    tags=["Company (per-source)"],
    summary="主要營業項目 / 所營事業（經濟部商工登記 GCIS）",
    description=(
        "**資料來源網站正式名稱**：經濟部商工登記公示資料 (GCIS) · 公司登記基本資料（資料集 `236EE382-...025E7C`）。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `GET https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C"
        "?$format=json&$filter=Business_Accounting_NO eq {TAX_ID}`\n"
        "- Input：OData filter 帶公司統一編號（`$` 需 URL-encode 為 `%24`）。\n"
        "- Output：JSON Array，內含 `Cmp_Business` 陣列。\n\n"
        "**處理邏輯**：\n"
        "1. 先從 TWSE/TPEx 基本資料中讀出 `tax_id`。\n"
        "2. 以 `tax_id` 並帶 OData filter 呼叫商工 API（24h 快取）。\n"
        "3. `Cmp_Business` 中 `Business_Item=''` 列為「敗述條目」，其他列為「行業分類」。\n"
        "4. 去除「１．」「2.」等前綴序號後輸出。"
    ),
)
async def api_company_business_items(stock_id: str):
    return await query_business_items(stock_id)


@app.get(
    "/api/company/{stock_id}/financials",
    response_model=FinancialsResponse,
    tags=["Company (per-source)"],
    summary="EPS / 淨利 / 營業利潤率（FinMind 季財報）",
    description=(
        "**資料來源網站正式名稱**：FinMind v4（免費 300 req/hr，無需 token）。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `GET https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements"
        "&data_id={stock_id}&start_date={as_of-5y}&end_date={as_of}`\n"
        "- Output：`{msg:'success', data:[{date, type, value}]}`。\n\n"
        "**處理邏輯**：\n"
        "1. 拉近 5 年資料（1h 快取）。\n"
        "2. 轉為 `{date: {type: value}}` map。\n"
        "3. `EPS` / `IncomeAfterTaxes` / `OperatingIncome` / `Revenue` 各別取 `date ≤ as_of` 最近 4 季 value 加總為 TTM；不足 4 季回 null。\n"
        "4. `operating_margin_pct = OperatingIncome(TTM) / Revenue(TTM) × 100`。"
    ),
)
async def api_company_financials(
    stock_id: str,
    as_of: str | None = Query(None, description=_AS_OF_DESC),
):
    return await query_financials(stock_id, as_of)


@app.get(
    "/api/company/{stock_id}/financials/yfinance",
    response_model=FinancialsResponse,
    tags=["Company (per-source)"],
    summary="EPS / 淨利 / 營業利潤率（yfinance 季財報、與 FinMind 版 spec 一致）",
    description=(
        "**資料來源網站正式名稱**：yfinance Python Library（Yahoo Finance 非官方 wrapper）。\n\n"
        "**資料源 API 用法**：\n"
        "- `yfinance.Ticker(\"{stock_id}.TW\")` 上市、`{stock_id}.TWO` 上櫃。\n"
        "- `ticker.quarterly_financials` 取得 income statement DataFrame（columns=財報日、"
        "index=欄位名）。\n"
        "- 欄位對應：`Basic EPS` → EPS、`Net Income` → 稅後淨利、"
        "`Operating Income` → 營業淨利、`Total Revenue` → 營收。\n\n"
        "**處理邏輯**（與原 endpoint 輸出欄位 100% 一致）：\n"
        "1. yfinance 台股 `quarterly_financials` 回傳的已是「單季值」（sandbox 驗證與 FinMind 單季誤差 < 1%），不需差分還原。\n"
        "2. 送 NaN / 缺值跳過後，轉為與 FinMind 同結構的 `{date, type, value}` rows。\n"
        "3. `EPS / IncomeAfterTaxes / OperatingIncome / Revenue` 各取 `date ≤ as_of` 最近 4 季 value 加總為 TTM；不足 4 季回 null。\n"
        "4. `operating_margin_pct = OperatingIncome(TTM) / Revenue(TTM) × 100`。\n\n"
        "**優點**：請求限制遠高於 FinMind（經驗值每小時可達數千次）、免費、無需 token。\n"
        "**缺點**：台股財報細目不如 FinMind 詳盡，偶有個別季欄位 NaN、標籤對應可能不精準；"
        "本 endpoint 遇 NaN / 缺值會跳過，靠「不足 4 季回 null」的保護避免錯誤結果。"
    ),
)
async def api_company_financials_yfinance(
    stock_id: str,
    as_of: str | None = Query(None, description=_AS_OF_DESC),
):
    return await query_financials_yfinance(stock_id, as_of)


@app.get(
    "/api/company/{stock_id}/revenue",
    response_model=RevenueResponse,
    tags=["Company (per-source)"],
    summary="月營收 + YoY + TTM（FinMind）",
    description=(
        "**資料來源網站正式名稱**：FinMind v4。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `GET https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue"
        "&data_id={stock_id}&start_date={as_of-5y}&end_date={as_of}`\n"
        "- Output：`data:[{date, revenue_year, revenue_month, revenue}]`。\n\n"
        "**處理邏輯**：\n"
        "1. 取 `date ≤ as_of` 排序後最新一筆 → `latest_month_*`。\n"
        "2. 找去年同月計算 `latest_month_yoy_pct = (本月 - 去年同月) / 去年同月 × 100`。\n"
        "3. 最近 12 個月加總 → `ttm_value`；再往前 12 個月加總 → 用來算 `ttm_yoy_pct`。\n"
        "4. 月份不足長度時個別欄位回 null。"
    ),
)
async def api_company_revenue(
    stock_id: str,
    as_of: str | None = Query(None, description=_AS_OF_DESC),
):
    return await query_revenue(stock_id, as_of)


@app.get(
    "/api/company/{stock_id}/revenue/twse",
    response_model=RevenueResponse,
    tags=["Company (per-source)"],
    summary="月營收 + YoY + TTM（證交所體系：TWSE/TPEx t187ap05 + MOPS t21sc03）",
    description=(
        "**資料來源網站正式名稱**：\n"
        "- 「最新一個月」：TWSE OpenAPI / TPEx OpenAPI（公開資料平台 t187ap05）\n"
        "- 「歷史月營收」：公開資訊觀測站 MOPS（採用 IFRSs 後每月營業收入彙總表 t21sc03）\n\n"
        "**資料源 API URL 與使用方法**：\n"
        "- t187ap05 上市：`GET https://openapi.twse.com.tw/v1/opendata/t187ap05_L`（JSON）\n"
        "- t187ap05 上櫃：`GET https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv`（CSV）\n"
        "- t21sc03 歷史：`GET https://mopsov.twse.com.tw/nas/t21/{sii|otc}/t21sc03_{民國YYY}_{M}_0.html`\n"
        "  ·民國YYY = 西元年 - 1911；M = 1~12；市場代碼 sii (上市) / otc (上櫃)\n"
        "  ·取得完整表格（8 欄以上、81 欄以下）並以 `公司代號 == stock_id` 過濾。\n\n"
        "**處理邏輯**（與 FinMind 版 endpoint 輸出欄位 100% 一致）：\n"
        "1. 依 basic 表 `market` 決定走 `_L`或`_O`；拿不到 basic 時兩個都試。\n"
        "2. **最新月**：一次取全市場後以 `公司代號` 過濾 → `latest_month_value`、`latest_month_yoy_pct`。\n"
        "3. **歷史**：以最新月為起點往回推 26 個月，併發抓取 MOPS t21sc03 HTML 頁並以 `公司代號` 過濾。\n"
        "4. 所有「當月營收」單位為仟元，×1000 換算為元後寫入。\n"
        "5. `latest_month_yoy_pct` 取 t187ap05 「去年同月增減(%)」；若為空則由 MOPS 歷史 fallback。\n"
        "6. `ttm_value` = 最新 12 個月營收加總；`ttm_yoy_pct` = (last12 總 - prev12 總) / prev12 總 × 100。\n"
        "7. 欄位值為空 / `-` / 無法轉為數字時，對應欄位回 `null`。\n\n"
        "**優點**：完全使用證交所 / 櫃買中心 + 公開資訊觀測站「官方」公開資料，免 token、免限流；\n"
        "資料沿革與《證交法》規定之「月營收公告（次月 10 日前）」同步。\n"
        "**缺點**：MOPS 頁面為 HTML（big5 編碼）需解析，首次跳 26 個月較慢；不過併發 + 24h TTL cache，同一公司重查及他公司查詢都接近即時。"
    ),
)
async def api_company_revenue_twse(
    stock_id: str,
    as_of: str | None = Query(None, description=_AS_OF_DESC),
):
    return await query_revenue_twse(stock_id, as_of)


@app.get(
    "/api/company/{stock_id}/dividend",
    response_model=DividendResponse,
    tags=["Company (per-source)"],
    summary="股利資訊（FinMind Dividend）",
    description=(
        "**資料來源網站正式名稱**：FinMind v4。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `GET https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividend"
        "&data_id={stock_id}&start_date={as_of-5y}&end_date={as_of}`\n"
        "- Output：`data:[{year, date, CashEarningsDistribution, CashStatutorySurplus, "
        "StockEarningsDistribution, StockStatutorySurplus, CashExDividendTradingDate, "
        "StockExDividendTradingDate, ...}]`。\n\n"
        "**處理邏輯**：\n"
        "1. 依 `CashExDividendTradingDate` / `StockExDividendTradingDate` / `date` 依序選「除息日 ≤ as_of」最後一次。\n"
        "2. 現金股利 = `CashEarningsDistribution + CashStatutorySurplus`；\n"
        "   股票股利 = `StockEarningsDistribution + StockStatutorySurplus`。\n"
        "3. 無合適股利時 `dividend=null`。"
    ),
)
async def api_company_dividend(
    stock_id: str,
    as_of: str | None = Query(None, description=_AS_OF_DESC),
):
    return await query_dividend(stock_id, as_of)


@app.get(
    "/api/company/{stock_id}/dividend/yfinance",
    response_model=DividendResponse,
    tags=["Company (per-source)"],
    summary="股利資訊（yfinance Ticker.dividends、與 FinMind 版 spec 一致）",
    description=(
        "**資料來源網站正式名稱**：yfinance Python Library（Yahoo Finance 非官方 wrapper）。\n\n"
        "**資料源 API 用法**：\n"
        "- `yfinance.Ticker(\"{stock_id}.TW\")` 上市、`{stock_id}.TWO` 上櫃。\n"
        "- `ticker.dividends` 取得歷年配息 Series（index=除息日 Timestamp、value=每股現金股利）。\n"
        "- 以 `ticker.actions` 替代亦可，但本 endpoint 只專注現金配息欄位。\n\n"
        "**處理邏輯**（與原 endpoint 輸出欄位 100% 一致）：\n"
        "1. 依 basic 表 `market` 決定 `.TW` / `.TWO` 後綴。\n"
        "2. 將 yfinance Series 轉為與 FinMind `TaiwanStockDividend` 同欄位的 rows："
        "   `CashEarningsDistribution`=每股現金股利、`CashExDividendTradingDate`=除息日；"
        "   yfinance 不提供股票股利 / 公告日 / 現金股利發放日，對應欄位為 `null` 或 0。\n"
        "3. 依 `CashExDividendTradingDate` 選「除息日 ≤ as_of」最後一次（共用原 `_pick_dividend`）。\n"
        "4. 無合適股利時 `dividend=null`。\n\n"
        "**優點**：yfinance 速度快，適合取單一股票的長期歷史資料；請求限制遠寬於 FinMind（經驗值每小時可達數千次）、免費、無需 token。\n"
        "**缺點**：歷史深度與資料完整性依 Yahoo Finance 支援程度而不同；股票股利 / 公告日 / 發放日不提供。"
    ),
)
async def api_company_dividend_yfinance(
    stock_id: str,
    as_of: str | None = Query(None, description=_AS_OF_DESC),
):
    return await query_dividend_yfinance(stock_id, as_of)


@app.get(
    "/api/company/{stock_id}/dividend/history",
    response_model=DividendHistoryResponse,
    tags=["Company (per-source)"],
    summary="股利完整歷史事件（v0.0.10、供 PoC SQL 層事件母體使用）",
    description=(
        "**資料來源網站正式名稱**：FinMind v4 開放金融資料庫。\n\n"
        "**目的**：不同於 `/dividend?as_of=X` 只回 as_of 前最後一筆，本 endpoint 回該公司歷史所有"
        "配息事件，供 PoC SQL 層 `dividend_event_list` 以「除息日」作為事件母體。\n\n"
        "**處理邏輯**：\n"
        "1. FinMind `TaiwanStockDividend`，取「往前 20 年 → 今天」的全量 rows。\n"
        "2. 以「具備 `CashExDividendTradingDate`」為必要條件（下游 _list 需以除息日為 as_of）。\n"
        "3. `events` 以 `cash_ex_dividend_date` DESC 排序；金額欄位缺失以 0 補齊（與 `_pick_dividend` 完全相容）。\n\n"
        "**使用情境**：PoC SQL `raw_dividend_history` 將本 endpoint 回包全量 array 写入 raw，"
        "`dividend_event_list.sql` 再攤平出 (stk_code, cash_ex_dividend_date) 供下游對 `/dividend?as_of=<除息日>` 呼叫。"
    ),
)
async def api_company_dividend_history(stock_id: str):
    return await query_dividend_history(stock_id)


@app.get(
    "/api/company/{stock_id}/dividend/history/yfinance",
    response_model=DividendHistoryResponse,
    tags=["Company (per-source)"],
    summary="股利完整歷史事件（yfinance Ticker.dividends 版，與 FinMind 版 spec 完全一致）",
    description=(
        "**資料來源網站正式名稱**：yfinance Python Library（Yahoo Finance non-official wrapper）。\n\n"
        "與 `/dividend/history` 同 spec，只換 upstream 為 yfinance `Ticker.dividends`。"
        "同樣供 PoC SQL 層 `dividend_event_list_yfinance` 以除息日為事件母體使用。\n\n"
        "**邊位限制**：yfinance 不提供股票股利 / 公告日 / 現金股利發放日，對應欄位為 `null` 或 0。"
    ),
)
async def api_company_dividend_history_yfinance(stock_id: str):
    return await query_dividend_history_yfinance(stock_id)


@app.get(
    "/api/product-revenue/filers",
    response_model=ProductRevenueFilersResponse,
    tags=["Company (per-source)"],
    summary="MOPS 該月該市場「各項產品業務營收」申報公司清單（v0.0.10、供 PoC SQL 事件母體使用）",
    description=(
        "**資料來源網站正式名稱**：MOPS 公開資訊觀測站（公開資訊觀測站 mops.twse.com.tw）。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `POST https://mops.twse.com.tw/mops/api/redirectToOld`，`apiName=ajax_t05st08_all`；`YM=民國年月 5碼`、`TYPEK=sii|otc`。\n"
        "- Output：該月該市場所有申報公司的 HTML 清單頁；以 regex 提取 `co_id.value='...'` 取得公司代碼。\n\n"
        "**目的**：MOPS 「各項產品業務營收」IFRS 後改自願申報，非所有公司每月都有申報。本 endpoint 直接"
        "列出「真正有申報的 (co_id, ym)」，供 PoC SQL 層 `product_revenue_filer_list` 作為事件母體。\n\n"
        "**參數**：`ym` = 民國年月 5碼（例 `11312` = 民國 113 年 12 月）；`market` = `sii`（上市）/ `otc`（上櫃）。"
    ),
)
async def api_product_revenue_filers(
    ym: str = Query(..., description="民國年月 5碼字串，例 `11312`。"),
    market: str = Query(..., description="市場別：`sii`（上市）/ `otc`（上櫃）。"),
):
    return await query_product_revenue_filers(ym, market)


@app.get(
    "/api/company/{stock_id}/value-chain",
    response_model=CompanyValueChainResponse,
    tags=["Company (per-source)"],
    summary="產業價值鏈定位 + 上下游鄰居（櫃買中心）",
    description=(
        "**資料來源網站正式名稱**：櫃買中心 · 產業價值鏈資訊平台 <https://ic.tpex.org.tw/>。\n\n"
        "**資料源 API URL 與呼叫方法**：\n"
        "- `GET https://ic.tpex.org.tw/introduce.php?ic={IC_CODE}`（server-rendered HTML、無 JSON API）。\n"
        "- Output：HTML 頁面，以 BeautifulSoup(lxml) 解析。\n\n"
        "**處理邏輯**：\n"
        "1. 首次查詢觸發 `ensure_loaded(background=True)`：以 semaphore=6 並行下載 47 條鏈頁面（~8 秒）。\n"
        "2. 解析 `main_ic_panel` / `sc-ind-pnl_{top}` / `sc_company_{sub}` 結構；有 `<b>本國</b>`、跳過 `<b>外國</b>`。\n"
        "3. 結果落盤至 `data/icchain.json`（TTL 7 天）。\n"
        "4. 本 endpoint：以 `stk_code` 反查 `company_index` 取出 `memberships`；再由 `chain_tree[ic_code]` 走訪出 `neighbors_by_chain`。\n\n"
        "**載入未完成時**：200 + `status='loading'` + `memberships=[]`。"
    ),
)
async def api_company_value_chain(stock_id: str):
    return await query_value_chain(stock_id)


@app.get(
    "/api/company/{stock_id}/product-revenue",
    response_model=ProductRevenueResponse,
    tags=["Company (per-source)"],
    summary="主要產品比重 / 各項產品業務營收統計表（MOPS）",
    description=(
        "**資料來源網站正式名稱**：公開資訊觀測站 (MOPS) · 各項產品業務營收統計表（內部代號 t05st08） "
        "<https://mops.twse.com.tw/mops/web/t05st08>。\n\n"
        "**資料源 API URL 與呼叫方法**（三步驟 HTTP 流程）：\n"
        "1. `POST https://mops.twse.com.tw/mops/api/redirectToOld`，JSON body：\n"
        "   `{\"apiName\":\"ajax_t05st08\",\"parameters\":{\"co_id\":<STOCK_ID>,\"isnew\":\"true\",\"encodeURIComponent\":1,\"step\":1,\"firstin\":1,\"off\":1}}` \n"
        "   → 回傳 `{result: {url: \"https://mopsov.twse.com.tw/mops/web/ajax_t05st08?parameters=<加密參數>\"}}`。\n"
        "2. `GET` 上一步 mopsov URL → 回傳 server-rendered HTML，內含 `autoForm`（隱藏欄位 year/month/TYPEK/yearmonth 等）。\n"
        "3. `POST https://mopsov.twse.com.tw/mops/web/ajax_t05st08`，`application/x-www-form-urlencoded`，"
        "帶入 step 2 拆出的所有欄位 → 回傳含 `<table class='hasBorder'>` 的最終 HTML 表。\n\n"
        "**反爬蟲注意事項**：必須帶 `User-Agent`、`Accept-Language: zh-TW`、`Origin`、`Referer` 才能避開 WAF（缺一即遇到 7641438215888200112 阻擋）。"
        "無需 cookie / CSRF / Authorization。\n\n"
        "**處理邏輯**：\n"
        "1. `httpx.AsyncClient(follow_redirects=True)` 依序執行三步驟。\n"
        "2. `_parse_mops_autoform()`：用 BeautifulSoup 抓 `<form name='autoForm'>` 內所有 `<input name=... value=...>`。\n"
        "3. `_parse_mops_table()`：解析 `<TABLE class='hasBorder'>`，逐列擷取 序號 `(1)~(10)` / `其他` / `減：銷貨退回及折讓` / `合計業務營收淨額`，"
        "金額一律由仟元 × 1000 轉為元。\n"
        "4. 百分比 = `amount / (total_revenue + sales_return)) × 100`，於後端計算（MOPS 原表並不一定提供 %）。\n"
        "5. 命中後寫入 `cachetools.TTLCache(maxsize=2048, ttl=6h)`（月報性質，6 小時快取足夠）。\n\n"
        "**回應特性**：\n"
        "- 年/月以民國年字串表示（例 `year='113'` = 民國 113 年 = 西元 2024）。\n"
        "- 採用 IFRSs 後採自願申報之公司若無申報，回傳 `found=false` + `notes='採用IFRSs後採自願申報，該公司無申報'`。\n"
        "- 上游連線/解析失敗時回傳 `found=false` 並於 `error` 欄位記錄錯誤型別與訊息（不丟 5xx）。\n\n"
        "**日期參數 `as_of`**：\n"
        "- 省略時：走「單一公司」流程（`ajax_t05st08`）— MOPS 只回該公司最後一次申報期間（如 2330→民國 109/12）。\n"
        "- 提供時：「兩階段加速回溯」流程。\n"
        "  1. 先用 `ajax_t05st08`（單一公司）拿該公司的 `last_filed_ym`（最後一次申報期），stock_id 為 key 快取 24h。\n"
        "  2. 若 `as_of ≥ last_filed_ym`→直接從 `last_filed_ym` 出發，首輪命中（最多只走 6 個月）。\n"
        "  3. 若 `as_of < last_filed_ym`→從 `as_of` 所在月份往回走，上限 240 個月（足以涵蓋 MOPS 本表所有歷史）。\n"
        "  4. 若 `last_filed_ym` 為 `None`（該公司未曾申報）→短路直接回 `found=false`。\n"
        "- 兩階段快取：`stock_id → last_filed_ym` (24h)、`(YM, TYPEK) → 申報名單` (6h、跨使用者共用)、`(YM, stock_id) → 明細` (6h)。"
    ),
)
async def api_company_product_revenue(
    stock_id: str,
    as_of: str | None = Query(
        None,
        description=(
            "查詢基準日 `YYYY-MM-DD`；省略則回「最後一次申報」。"
            "提供時會限定在該日期之前（含當月）可找到的「最晚申報月份」。"
            "在 IFRSs 后停止申報之公司（如 2330=民國 109/12，2454=108/12），若 `as_of` 早於這個月份则會查不到。"
        ),
    ),
):
    return await query_product_revenue(stock_id, as_of)


# =====================================================================
# Market Data
# =====================================================================
@app.get(
    "/api/ohlcv",
    response_model=OhlcvResponse,
    tags=["Market Data"],
    summary="日 K 歷史行情（上市 + 上櫃整合，智能切換上游）",
    description=(
        "取得指定股票、指定日期範圍的日 K OHLCV。上市 / 上櫃自動別從 `company_basic_info` 判別，"
        "已把成交股數、成交金額單位對齊到「股 / 元」。\n\n"
        "**智能切換** (門檻 `range_days ≤ 7`)：\n"
        "* ≤ 7 天 → `per_day_market`：逐日呼叫全市場 endpoint（MI_INDEX / dailyQuotes），"
        "backend 磁碟 cache `/tmp/ohlcv_cache/{market}/{YYYYMMDD}.json.gz`，同一日多股查詢共享下載。\n"
        "* > 7 天 → `per_stock_month`：逐月呼叫單股單月 endpoint（STOCK_DAY / tradingStock），"
        "一次 payload 涵蓋約 20 個交易日、成本約 1/20。\n\n"
        "**上游 endpoint**：\n"
        "* 上市 per-day：<https://www.twse.com.tw/exchangeReport/MI_INDEX>（Big5 / ms950 CSV）\n"
        "* 上市 per-month：<https://www.twse.com.tw/exchangeReport/STOCK_DAY> `?response=json&date=YYYYMMDD&stockNo=XXXX`\n"
        "* 上櫃 per-day：<https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes> `?date=YYYY/MM/DD&type=EW&response=json`\n"
        "* 上櫃 per-month：<https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock> `?code=XXXX&date=YYYY/MM/DD&response=json`\n\n"
        "**單位正規化**：TPEx 上游回「張 / 仟元」，backend 自動 ×1000 對齊到「股 / 元」。\n"
        "**範圍限制**：1 天 ≤ `to_date - from_date` ≤ 366 天，超過回 400。"
    ),
)
async def ohlcv(
    stk_code: str = Query(
        ...,
        min_length=1,
        max_length=10,
        description="股票代號（例 `2330`）。必須能在 `company_basic_info` 中找到，否則 `found=false`。",
    ),
    from_date: str = Query(
        ...,
        alias="from",
        description="起始日（含，`YYYY-MM-DD` 西元）。",
    ),
    to_date: str = Query(
        ...,
        alias="to",
        description="結束日（含，`YYYY-MM-DD` 西元）。",
    ),
):
    try:
        d_from = datetime.strptime(from_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="`from` must be YYYY-MM-DD")
    try:
        d_to = datetime.strptime(to_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="`to` must be YYYY-MM-DD")
    if d_to < d_from:
        raise HTTPException(status_code=400, detail="`to` must be ≥ `from`")
    if (d_to - d_from).days + 1 > ohlcv_source.MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"date range too large (max {ohlcv_source.MAX_RANGE_DAYS} days)",
        )
    return await ohlcv_source.get_ohlcv(stk_code.strip(), d_from, d_to)


# =====================================================================
# Static frontend
# =====================================================================
# 靜態前端：直接把 static/ 挂在根路徑，讓 ./style.css ./app.js 可直接讀取
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/style.css", include_in_schema=False)
    async def _style():
        return FileResponse(str(STATIC_DIR / "style.css"))

    @app.get("/app.js", include_in_schema=False)
    async def _appjs():
        return FileResponse(str(STATIC_DIR / "app.js"))
