"""FastAPI 入口。

本檔同時負責產出 Swagger / OpenAPI 文件 — 每個 endpoint 都標註：
1. 上游資料源網站正式名稱
2. 上游 API URL 與呼叫方式（input / output）
3. 上游回應如何被處理成本 endpoint 的輸出
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import icchain
from .schemas import (
    ChainResponse,
    ChainsListResponse,
    CompanyResponse,
    HealthResponse,
    SearchResponse,
)
from .service import query, search_companies

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
        {"name": "Company", "description": "公司資料 / 搜尋"},
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
    chain = icchain.get_chain(ic_code)
    if not chain:
        raise HTTPException(status_code=404, detail=f"查無產業鏈 {ic_code}")
    return chain


# =====================================================================
# Company
# =====================================================================
@app.get(
    "/api/search",
    response_model=SearchResponse,
    tags=["Company"],
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
    tags=["Company"],
    summary="查詢一家公司的基本資料 + KPI + 產業價值鏈定位",
    description=(
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
