"""Pydantic 回應模型 — 每個欄位的 description 詳述背後資料源、API URL、呼叫方式與處理邏輯。

設計原則：
- 每個欄位 description 至少包含三段資訊：
    1. 來源網站正式名稱
    2. 上游 API URL / 呼叫方法（GET / POST、必要參數）
    3. 上游 API 回應如何被轉換成本欄位（key 對照、單位、TTM 計算規則 ...）
- 所有模型統一 Config(extra="allow")，避免上游 schema 變動造成 500。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# =====================================================================
# 共用欄位（資料源說明片段，方便重用）
# =====================================================================

_TWSE_BASIC_DESC = (
    "資料源：**證券交易所 (TWSE) OpenAPI · 上市公司基本資料**。\n"
    "API：`GET https://openapi.twse.com.tw/v1/opendata/t187ap03_L`（無需 token、無參數，每日刷新）。\n"
    "回傳 JSON Array，每筆是一家上市公司的中文欄位 dict。"
)
_TPEX_BASIC_DESC = (
    "資料源：**櫃買中心 (TPEx) OpenAPI · 上櫃公司基本資料**。\n"
    "API：`GET https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`（無需 token、無參數，每日刷新）。\n"
    "回傳 JSON Array，欄位為英文 key（SecuritiesCompanyCode、CompanyName ...）。"
)
_FINMIND_DESC = (
    "資料源：**FinMind v4**（免費 300 req/hr，無需 token）。\n"
    "API：`GET https://api.finmindtrade.com/api/v4/data?dataset={DATASET}&data_id={STOCK_ID}&start_date={START}&end_date={END}`。\n"
    "回傳格式 `{'msg': 'success', 'data': [...]}`，本服務取 `data` 陣列做後處理。"
)
_GCIS_DESC = (
    "資料源：**經濟部商工登記公示資料 (GCIS) · 公司登記基本資料**（資料集 ID `236EE382-4942-41A9-BD03-CA0709025E7C`）。\n"
    "API：`GET https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C?$format=json&$filter=Business_Accounting_NO eq {TAX_ID}`。\n"
    "（OData 過濾語法，`$` 需 URL-encode 成 `%24`。）回傳 JSON Array，內含 `Cmp_Business` 子陣列即所營事業條目。"
)
_TPEX_ICCHAIN_DESC = (
    "資料源：**櫃買中心 · 產業價值鏈資訊平台**（`https://ic.tpex.org.tw/`）。\n"
    "API：`GET https://ic.tpex.org.tw/introduce.php?ic={IC_CODE}`（無 JSON API，僅 server-rendered HTML）。\n"
    "本服務首次查詢時 lazy 背景全量收集 47 條產業鏈頁面（semaphore=6 併發，~8 秒），\n"
    "以 BeautifulSoup(lxml) 解析 `main_ic_panel` / `sc-ind-pnl_*` / `sc_company_*` 結構，\n"
    "落盤至 `data/icchain.json`，TTL 7 天。公司比對採純 `stk_code` 反查（嚴謹，不做模糊比對）。"
)


# =====================================================================
# /api/health
# =====================================================================
class IcChainStatus(BaseModel):
    """產業價值鏈快取載入狀態。"""
    model_config = ConfigDict(extra="allow")

    loaded: bool = Field(..., description="是否已完成 47 條產業鏈的解析、可供查詢")
    loading: bool = Field(..., description="是否正在背景載入中")
    chain_count: int = Field(..., description="已成功解析的產業鏈數量（成功時為 47）")
    indexed_companies: int = Field(..., description="company_index 收錄的上市櫃公司家數（約 1850 家）")
    fetched_at: Optional[float] = Field(None, description="上次成功載入的 unix timestamp（秒）；None 代表本程序尚未載入過")
    errors: list[Any] = Field(default_factory=list, description="最近幾筆載入錯誤訊息（最多 5 筆）")


class HealthResponse(BaseModel):
    """`GET /api/health` 回應。"""
    model_config = ConfigDict(extra="allow")

    ok: bool = Field(..., description="服務基本可用性旗標")
    icchain: IcChainStatus = Field(..., description="產業價值鏈背景任務狀態")


# =====================================================================
# /api/search
# =====================================================================
class SearchHit(BaseModel):
    """搜尋結果單筆。"""
    model_config = ConfigDict(extra="allow")

    stock_id: str = Field(
        ...,
        description=(
            "股票代號。\n"
            + _TWSE_BASIC_DESC
            + "\n→ 取自上市資料 `公司代號` 欄位。\n\n"
            + _TPEX_BASIC_DESC
            + "\n→ 取自上櫃資料 `SecuritiesCompanyCode` 欄位。"
        ),
    )
    company_name: Optional[str] = Field(
        None,
        description=(
            "公司全名。"
            + _TWSE_BASIC_DESC + "（`公司名稱`）；"
            + _TPEX_BASIC_DESC + "（`CompanyName`）。"
        ),
    )
    short_name: Optional[str] = Field(None, description="公司簡稱（TWSE `公司簡稱` / TPEx `CompanyAbbreviation`）。")
    market: Optional[str] = Field(None, description="`上市` 或 `上櫃`（由命中表決定）。")
    industry_name: Optional[str] = Field(
        None,
        description=(
            "產業別中文名。TWSE/TPEx 基本資料只回傳數字代碼（`產業別` / `SecuritiesIndustryCode`），\n"
            "本服務以 `app/industry.py` 內建的代碼表轉成中文（例如 `24` → `半導體業`）。"
        ),
    )


class SearchResponse(BaseModel):
    """`GET /api/search` 回應。"""
    model_config = ConfigDict(extra="allow")

    results: list[SearchHit] = Field(
        ...,
        description=(
            "符合關鍵字的公司列表。\n\n"
            "**資料源處理流程**：\n"
            "1. 程序啟動或快取過期時，並行呼叫 " + _TWSE_BASIC_DESC + "\n"
            "2. 並行呼叫 " + _TPEX_BASIC_DESC + "\n"
            "3. 合併兩份清單為 `stock_id -> 公司資料` dict，存入 24h TTL 記憶體快取。\n"
            "4. 收到搜尋請求時，對代號 / 公司全名 / 簡稱 / 英文簡稱做大小寫不敏感子字串比對，至多回 `limit` 筆。"
        ),
    )


# =====================================================================
# /api/company/{stock_id}
# =====================================================================
class BusinessItems(BaseModel):
    """主要營業項目（所營事業）。"""
    model_config = ConfigDict(extra="allow")

    narrative: list[str] = Field(
        ...,
        description=(
            "公司自行撰寫的敘述條目（最具識別度，例如台積電「依客戶之訂單製造與銷售積體電路…」）。\n"
            + _GCIS_DESC + "\n"
            "→ 取自 `Cmp_Business` 陣列中 `Business_Item` 為空字串的元素的 `Business_Item_Desc` 欄位，\n"
            "去除前綴序號（「１．」「2.」等）後輸出。"
        ),
    )
    categories: list[dict] = Field(
        ...,
        description=(
            "標準行業分類（中華民國行業標準分類代碼）。\n"
            + _GCIS_DESC + "\n"
            "→ 取自 `Cmp_Business` 陣列中 `Business_Item` 為純行業代碼的元素，"
            "輸出 `{code, desc}`，desc 對應 `Business_Item_Desc`。"
        ),
    )


class CompanyBasic(BaseModel):
    """公司基本資料區塊。"""
    model_config = ConfigDict(extra="allow")

    market: Optional[str] = Field(None, description="上市 / 上櫃。決定走 TWSE 或 TPEx 來源。")
    company_name: Optional[str] = Field(None, description=_TWSE_BASIC_DESC + "（`公司名稱`） / " + _TPEX_BASIC_DESC + "（`CompanyName`）。")
    short_name: Optional[str] = Field(None, description="`公司簡稱` / `CompanyAbbreviation`。")
    english_name: Optional[str] = Field(None, description="`英文簡稱` / `Symbol`。")
    tax_id: Optional[str] = Field(
        None,
        description=(
            "營利事業統一編號（8 碼）。`營利事業統一編號` / `UnifiedBusinessNo.`。"
            "本服務再以此值帶入經濟部商工 API，查詢所營事業。"
        ),
    )
    paid_in_capital: Optional[int] = Field(
        None,
        description="實收資本額（新台幣元）。`實收資本額` / `Paidin.Capital.NTDollars`，字串去逗號後轉 int。",
    )
    industry_code: Optional[str] = Field(None, description="TWSE/TPEx 產業別代碼（數字字串）。")
    industry_name: Optional[str] = Field(
        None,
        description="產業別代碼經 `app/industry.py` 內建表轉換成中文名（例如 `24` → `半導體業`）。",
    )
    business_items: BusinessItems = Field(..., description="主要營業項目（拆成敘述條目 + 行業分類）。")
    general_manager: Optional[str] = Field(None, description="總經理姓名。`總經理` / `GeneralManager`。")
    chairman: Optional[str] = Field(None, description="董事長姓名。`董事長` / `Chairman`。")
    incorporation_date: Optional[str] = Field(
        None,
        description=(
            "公司成立日期。`成立日期` / `DateOfIncorporation`。\n"
            "原始格式可能為 7 位民國日期（如 `1150508`）或 8 位西元（`19870221`），本服務統一轉成 `YYYY-MM-DD`。"
        ),
    )
    listing_date: Optional[str] = Field(None, description="上市/上櫃日期，格式化規則同 `incorporation_date`。")
    website: Optional[str] = Field(None, description="公司網址。`網址` / `WebAddress`。")
    address: Optional[str] = Field(None, description="公司住址。`住址` / `Address`。")


class EpsSection(BaseModel):
    """EPS 區塊。"""
    model_config = ConfigDict(extra="allow")

    ttm: Optional[float] = Field(
        None,
        description=(
            "TTM (trailing twelve months) EPS。\n"
            + _FINMIND_DESC + "\n"
            "→ dataset=`TaiwanStockFinancialStatements`，篩選 `type='EPS'`，"
            "取 `date ≤ as_of` 的最近 4 季 `value` 加總。不足 4 季回 null。"
        ),
    )
    ttm_quarters: list[str] = Field(
        ...,
        description="計算 TTM 時實際使用的 4 個季財報日期（最新→最舊），格式 `YYYY-MM-DD`。",
    )
    latest_quarter_value: Optional[float] = Field(None, description="最近一季的單季 EPS（FinMind 同一資料集）。")
    latest_quarter_date: Optional[str] = Field(None, description="最近一季的財報日期。")


class RevenueSection(BaseModel):
    """營收區塊。"""
    model_config = ConfigDict(extra="allow")

    latest_month_label: Optional[str] = Field(
        None,
        description=(
            "最近一個月營收的年/月標籤（例如 `2026/04`）。\n"
            + _FINMIND_DESC + "\n"
            "→ dataset=`TaiwanStockMonthRevenue`；以 `date ≤ as_of` 排序取最新，"
            "由回傳的 `revenue_year` + `revenue_month` 拼成 `YYYY/MM`。"
        ),
    )
    latest_month_value: Optional[int] = Field(None, description="最近一個月的 `revenue` 欄位（新台幣元）。")
    latest_month_yoy_pct: Optional[float] = Field(
        None,
        description="該月年增率（%）。從同 dataset 中找去年同月，公式 `(本月-去年同月)/去年同月 × 100`。",
    )
    ttm_value: Optional[int] = Field(
        None,
        description="最近 12 個完整月份營收加總（TTM 月營收）。資料來源同上；月份不足 12 筆回 null。",
    )
    ttm_yoy_pct: Optional[float] = Field(
        None,
        description="TTM 營收年增率（%）：最近 12 個月加總 vs. 再往前 12 個月加總。需要 24 筆月營收。",
    )
    ttm_from_financial_statements: Optional[float] = Field(
        None,
        description=(
            "從季財報 `Revenue` 欄位加總而成的 TTM（用以計算營業利潤率）。"
            "資料源同 EPS：FinMind `TaiwanStockFinancialStatements`，`type='Revenue'`。"
        ),
    )


class NetIncomeSection(BaseModel):
    """淨利區塊。"""
    model_config = ConfigDict(extra="allow")

    ttm: Optional[float] = Field(
        None,
        description=(
            "TTM 稅後淨利。" + _FINMIND_DESC + "\n"
            "→ dataset=`TaiwanStockFinancialStatements`，`type='IncomeAfterTaxes'`，最近 4 季加總。"
        ),
    )
    ttm_quarters: list[str] = Field(..., description="加總時實際使用的 4 季日期。")
    latest_quarter_value: Optional[float] = Field(None, description="最近一季單季稅後淨利。")
    latest_quarter_date: Optional[str] = Field(None, description="最近一季財報日期。")


class DividendSection(BaseModel):
    """股利資訊。"""
    model_config = ConfigDict(extra="allow")

    year: Optional[Any] = Field(None, description="股利所屬年度（FinMind 原始欄位 `year`）。")
    reference_date: Optional[str] = Field(None, description="本服務挑選使用的「除息日 ≤ as_of」基準日。")
    cash_dividend: Optional[float] = Field(
        None,
        description=(
            "每股現金股利（元）。" + _FINMIND_DESC + "\n"
            "→ dataset=`TaiwanStockDividend`，本欄為 `CashEarningsDistribution + CashStatutorySurplus`。"
        ),
    )
    stock_dividend: Optional[float] = Field(
        None,
        description="每股股票股利（元）。同上 dataset，`StockEarningsDistribution + StockStatutorySurplus`。",
    )
    cash_ex_dividend_date: Optional[str] = Field(None, description="現金股利除息交易日（`CashExDividendTradingDate`）。")
    cash_payment_date: Optional[str] = Field(None, description="現金股利發放日（`CashDividendPaymentDate`）。")
    stock_ex_dividend_date: Optional[str] = Field(None, description="股票股利除權交易日（`StockExDividendTradingDate`）。")
    announcement_date: Optional[str] = Field(None, description="股利公告日（`AnnouncementDate`）。")


# ---- value_chain ----
class ChainMembership(BaseModel):
    """某公司在某條產業鏈中的定位。"""
    model_config = ConfigDict(extra="allow")

    ic_code: str = Field(..., description="產業鏈代碼，例如 `D000`（半導體）。")
    ic_name: str = Field(..., description="產業鏈中文名，例如 `半導體`。")
    segment: str = Field(..., description="上中下游：`上游` / `中游` / `下游`（值取自 HTML 的 `chain-title-panel` 文字）。")
    top_code: str = Field(..., description="上中下游下層分類的代碼（HTML 元素 `ic_link_{top_code}`）。")
    top_name: str = Field(..., description="上中下游下層分類的名稱，例如 `IC/晶圓製造`。")
    sub_code: str = Field(..., description="子分類代碼（HTML `sc_link_{sub_code}`）。")
    sub_name: str = Field(..., description="子分類名稱，例如 `晶圓製造`。")


class ChainNeighborCompany(BaseModel):
    model_config = ConfigDict(extra="allow")

    stk_code: str = Field(..., description="股票代號。")
    name: str = Field(..., description="公司名稱（HTML 表格的中文名欄位）。")
    top_name: str = Field(..., description="該公司所屬的上/中/下游下層分類名稱。")
    sub_name: str = Field(..., description="該公司所屬的子分類名稱。")
    is_self: bool = Field(..., description="是否為當次查詢的公司本身（用於前端高亮）。")


class ChainNeighbors(BaseModel):
    model_config = ConfigDict(extra="allow")

    ic_name: str = Field(..., description="產業鏈中文名。")
    upstream: list[ChainNeighborCompany] = Field(..., description="上游公司清單（同鏈、segment=上游 的去重結果）。")
    midstream: list[ChainNeighborCompany] = Field(..., description="中游公司清單。")
    downstream: list[ChainNeighborCompany] = Field(..., description="下游公司清單。")


class ValueChainSection(BaseModel):
    """產業價值鏈定位區塊。"""
    model_config = ConfigDict(extra="allow")

    status: str = Field(
        ...,
        description=(
            "載入狀態：`ready` / `loading` / `unavailable`。\n"
            + _TPEX_ICCHAIN_DESC + "\n"
            "→ 首次查詢觸發背景任務後，當下回 `loading`；快取就緒後改 `ready`。"
        ),
    )
    memberships: list[ChainMembership] = Field(
        ...,
        description=(
            "本公司出現在哪些產業鏈、上中下游、子分類。\n"
            + _TPEX_ICCHAIN_DESC + "\n"
            "→ 從 `company_index[stk_code]` 直接取出。同一公司可橫跨多條產業鏈 / 多個子分類。"
        ),
    )
    neighbors_by_chain: dict[str, ChainNeighbors] = Field(
        ...,
        description=(
            "依產業鏈代碼分組，列出該鏈所有上/中/下游公司。\n"
            "→ 從 `chain_tree[ic_code]` 走訪 segments → 上中下游下層分類 → 子鏈 → 公司表，"
            "並按 `stk_code` 去重後輸出。"
        ),
    )


class CompanyResponse(BaseModel):
    """`GET /api/company/{stock_id}` 完整回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否成功找到公司基本資料。")
    stock_id: str = Field(..., description="查詢使用的股票代號。")
    as_of: str = Field(..., description="實際使用的查詢基準日（`YYYY-MM-DD`）。")
    basic: CompanyBasic = Field(..., description="公司基本資料區塊，來源為 TWSE/TPEx 基本資料 + 經濟部商工所營事業。")
    eps: EpsSection = Field(..., description="EPS 區塊，來源 FinMind `TaiwanStockFinancialStatements`。")
    revenue: RevenueSection = Field(..., description="營收區塊，來源 FinMind `TaiwanStockMonthRevenue` + `FinancialStatements`。")
    net_income: NetIncomeSection = Field(..., description="淨利區塊，來源 FinMind `TaiwanStockFinancialStatements`。")
    operating_margin_pct: Optional[float] = Field(
        None,
        description=(
            "營業利潤率（%）。公式 `OperatingIncome(TTM) / Revenue(TTM) × 100`，"
            "兩個 TTM 皆從 FinMind `TaiwanStockFinancialStatements` 取近 4 季加總。"
        ),
    )
    dividend: Optional[DividendSection] = Field(None, description="股利區塊（FinMind `TaiwanStockDividend`）。")
    sources: list[str] = Field(..., description="本次回應實際使用過的上游資料源名稱清單。")
    value_chain: ValueChainSection = Field(..., description="產業價值鏈定位 + 上下游鄰居公司。")


# =====================================================================
# /api/chains 與 /api/chain/{ic_code}
# =====================================================================
class ChainListItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    ic_code: str = Field(..., description="產業鏈代碼，例如 `D000`。")
    ic_name: str = Field(..., description="產業鏈中文名，例如 `半導體`。")


class ChainsListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    chains: list[ChainListItem] = Field(
        ...,
        description=(
            "47 條產業鏈代碼與名稱列表。\n"
            + _TPEX_ICCHAIN_DESC + "\n"
            "→ 本清單由 `app/icchain.py::IC_CHAINS` 常數定義（依研究報告整理自 `ic.tpex.org.tw` 上的下拉選單）。"
        ),
    )
    status: IcChainStatus = Field(..., description="目前快取載入狀態。")


class SubChainCompany(BaseModel):
    model_config = ConfigDict(extra="allow")

    stk_code: str = Field(..., description="股票代號（取自 HTML `sc_company_{sub_code}` 表格內第一欄）。")
    name: str = Field(..., description="公司名稱（同表格第二欄）。")


class SubChain(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub_code: str = Field(..., description="子鏈代碼（HTML `sc_link_{sub_code}`）。")
    sub_name: str = Field(..., description="子鏈名稱。")
    companies: list[SubChainCompany] = Field(
        ...,
        description="該子鏈內的本國公司清單。HTML 內 `<b>外國</b>` 區段會被略過，只保留 `<b>本國</b>` 區段。",
    )


class TopChain(BaseModel):
    model_config = ConfigDict(extra="allow")

    top_code: str = Field(..., description="上中下游下層分類代碼（HTML `ic_link_{top_code}`）。")
    top_name: str = Field(..., description="上中下游下層分類名稱，例如 `IC/晶圓製造`。")
    sub_chains: list[SubChain] = Field(
        ...,
        description=(
            "該下層分類底下的子鏈陣列。若該分類沒有 sub-chain 結構，"
            "退回直接讀 `companyList_{top_code}`，並輸出單一 sub_chain（sub_code/sub_name 與 top 同名）。"
        ),
    )


class ChainResponse(BaseModel):
    """`GET /api/chain/{ic_code}` 回應。"""
    model_config = ConfigDict(extra="allow")

    ic_code: str = Field(..., description="產業鏈代碼。")
    ic_name: str = Field(..., description="產業鏈中文名。")
    segments: dict[str, list[TopChain]] = Field(
        ...,
        description=(
            "鍵為 `上游` / `中游` / `下游`，值為該段位下層分類列表。\n"
            + _TPEX_ICCHAIN_DESC + "\n"
            "→ 解析 `main_ic_panel` 內每個 `<div class=\"chain\">` 的 `chain-title-panel` 文字決定段位，"
            "再列出該段位下的 `ic_link_{top_code}` 節點。"
        ),
    )


# =====================================================================
# 6 個拆分後的獨立 endpoints
# =====================================================================
class CompanyBasicResponse(BaseModel):
    """`GET /api/company/{stock_id}/basic` 回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否找到這家公司的上市/上櫃基本資料。")
    stock_id: str = Field(..., description="查詢使用的股票代號。")
    market: Optional[str] = Field(None, description="`上市` / `上櫃`。")
    company_name: Optional[str] = Field(None, description=_TWSE_BASIC_DESC + "。`公司名稱` / " + _TPEX_BASIC_DESC + " `CompanyName`。")
    short_name: Optional[str] = Field(None, description="公司簡稱。`公司簡稱` / `CompanyAbbreviation`。")
    english_name: Optional[str] = Field(None, description="英文簡稱。`英文簡稱` / `Symbol`。")
    tax_id: Optional[str] = Field(None, description="營利事業統一編號（8 碼）。`營利事業統一編號` / `UnifiedBusinessNo.`。")
    paid_in_capital: Optional[int] = Field(None, description="實收資本額（新台幣元）。`實收資本額` / `Paidin.Capital.NTDollars`，字串去逗號轉 int。")
    industry_code: Optional[str] = Field(None, description="TWSE/TPEx 產業別代碼。")
    industry_name: Optional[str] = Field(None, description="產業別中文名（代碼經 `app/industry.py` 對照表轉換）。")
    general_manager: Optional[str] = Field(None, description="總經理。")
    chairman: Optional[str] = Field(None, description="董事長。")
    incorporation_date: Optional[str] = Field(None, description="公司成立日（民國年或西元統一轉 `YYYY-MM-DD`）。")
    listing_date: Optional[str] = Field(None, description="上市/上櫃日期。")
    website: Optional[str] = Field(None, description="公司網址。")
    address: Optional[str] = Field(None, description="公司住址。")
    source: Optional[str] = Field(None, description="本筆資料來源註記。")
    error: Optional[str] = Field(None, description="查無資料時的說明（作為 200 + found=false 返回者的補充）。")


class BusinessItemsResponse(BaseModel):
    """`GET /api/company/{stock_id}/business-items` 回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否找到公司。")
    stock_id: str = Field(..., description="查詢股票代號。")
    tax_id: Optional[str] = Field(None, description="營利事業統一編號（由 TWSE/TPEx 基本資料讀出，再用來查商工 API）。")
    narrative: list[str] = Field(
        default_factory=list,
        description=(
            "公司自行撰寫的敗述條目。\n" + _GCIS_DESC + "\n"
            "→ `Cmp_Business` 陣列中 `Business_Item` 為空字串的元素的 `Business_Item_Desc`，去除前綴序號。"
        ),
    )
    categories: list[dict] = Field(
        default_factory=list,
        description=(
            "中華民國行業標準分類代碼。\n" + _GCIS_DESC + "\n"
            "→ `Cmp_Business` 陣列中 `Business_Item` 為純行業代碼的元素，輸出 `{code, desc}`。"
        ),
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")
    error: Optional[str] = Field(None, description="查無資料時的說明。")


class FinancialsResponse(BaseModel):
    """`GET /api/company/{stock_id}/financials` 回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否有資料。")
    stock_id: str = Field(..., description="查詢股票代號。")
    as_of: str = Field(..., description="查詢基準日。")
    eps: EpsSection = Field(..., description="EPS 區塊（FinMind `TaiwanStockFinancialStatements` `type='EPS'`，最近 4 季加總）。")
    net_income: NetIncomeSection = Field(..., description="稅後淨利區塊（`type='IncomeAfterTaxes'`）。")
    operating_margin_pct: Optional[float] = Field(
        None,
        description="營業利潤率 %。`OperatingIncome(TTM) / Revenue(TTM) × 100`，兩端都來自 FinMind `TaiwanStockFinancialStatements`。",
    )
    revenue_ttm_from_financial_statements: Optional[float] = Field(
        None,
        description="從季財報 `Revenue` 加總出來的 TTM 營收（與 `revenue.ttm_value` 來源不同；外顯出來供比對、用於算營業利潤率）。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class RevenueResponse(BaseModel):
    """`GET /api/company/{stock_id}/revenue` 回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否有資料。")
    stock_id: str = Field(..., description="查詢股票代號。")
    as_of: str = Field(..., description="查詢基準日。")
    latest_month_label: Optional[str] = Field(None, description="最近一個月的年/月標籤（例 `2026/04`）。")
    latest_month_value: Optional[int] = Field(None, description="最近一個月營收（新台幣元）。")
    latest_month_yoy_pct: Optional[float] = Field(None, description="該月年增率 %。")
    ttm_value: Optional[int] = Field(None, description="最近 12 個完整月份營收加總（TTM 月營收）。")
    ttm_yoy_pct: Optional[float] = Field(None, description="TTM 營收年增率：最近 12 月 vs. 再往前 12 月。")
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class DividendResponse(BaseModel):
    """`GET /api/company/{stock_id}/dividend` 回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否有資料。")
    stock_id: str = Field(..., description="查詢股票代號。")
    as_of: str = Field(..., description="查詢基準日。")
    dividend: Optional[DividendSection] = Field(
        None,
        description="「除息日 ≤ as_of」最後一次股利；FinMind `TaiwanStockDividend`。無合適股利時為 null。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class DividendHistoryResponse(BaseModel):
    """`GET /api/company/{stock_id}/dividend/history` 回應（v0.0.10，rule 15 事件母體）。

    目的：供 PoC SQL 層建立 `dividend_event_list` 使用。每公司一次回完整歷史除息事件，
    避免以規則時間格點重複呼叫 `/dividend?as_of=...`。
    """
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否正常回應（不代表一定有事件；無事件時 events 為空 array）。")
    stock_id: str = Field(..., description="查詢股票代號。")
    events: list[DividendSection] = Field(
        default_factory=list,
        description="歷史所有配息事件，以 `cash_ex_dividend_date` DESC 排序。欄位同 `DividendSection`。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記（FinMind or yfinance）。")


class ProductRevenueFilersResponse(BaseModel):
    """`GET /api/product-revenue/filers` 回應（v0.0.10，rule 15 事件母體）。

    MOPS `ajax_t05st08_all` 的對外 wrapper：列出指定民國年月 (`ym`) 與市場 (`market`)下
    真正申報「各項產品業務營收」的公司代碼清單。供 PoC SQL 層 `product_revenue_filer_list`
    使用，避免對未申報的 (公司, 月份) 打 as_of API。
    """
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否正常回應（空清單也回 True）。")
    ym: str = Field(..., description="民國年月 5碼字串，例 `11312`。")
    market: str = Field(..., description="市場別：`sii`（上市）/ `otc`（上櫃）。")
    co_ids: list[str] = Field(
        default_factory=list,
        description="該月該市場於 MOPS t05st08_all 申報的公司代碼清單（升序）。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class CompanyValueChainResponse(BaseModel):
    """`GET /api/company/{stock_id}/value-chain` 回應。"""
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否有資料。")
    stock_id: str = Field(..., description="查詢股票代號。")
    status: str = Field(..., description="載入狀態：`ready` / `loading` / `unavailable`。")
    memberships: list[ChainMembership] = Field(
        default_factory=list,
        description="本公司出現在哪些產業鏈、上中下游、子分類。",
    )
    neighbors_by_chain: dict[str, ChainNeighbors] = Field(
        default_factory=dict,
        description="依產業鏈代碼分組，列出該鏈各分段下的公司（`streams` 彈性表達任意分段名稱）。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class ProductRevenueItem(BaseModel):
    """單一產品項目（出現在「各項產品業務營收統計表」中）。"""
    model_config = ConfigDict(extra="allow")

    rank: str = Field(..., description="產品序號標籤，例如 `(1)`、`(2)`、`其他`。")
    name: str = Field(..., description="產品/業務項目名稱。")
    amount: int = Field(..., description="該項目營收金額（新台幣元；原始 HTML 為仟元，已乘以 1000）。")
    percentage: Optional[float] = Field(
        None,
        description="該項目占合計業務營收淨額 + 銷貨退回及折讓的百分比 %。",
    )


class ProductRevenueResponse(BaseModel):
    """`GET /api/company/{stock_id}/product-revenue` 回應。

    資料來自公開資訊觀測站（MOPS）「各項產品業務營收統計表」(t05st08)。
    """
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否有解析到任何產品項目資料。")
    stock_id: str = Field(..., description="查詢股票代號。")
    as_of: str = Field(..., description="查詢基準日。")
    year: Optional[str] = Field(
        None,
        description="申報年度（民國年，字串，例如 `113` 表示民國 113 年 / 西元 2024）。",
    )
    month: Optional[str] = Field(
        None,
        description="申報月份（zero-padded 兩位數字串，例如 `12`）。月報為公司最近一次申報期間。",
    )
    company_name: Optional[str] = Field(None, description="公司名稱（由 MOPS 表頭解析得到）。")
    items: list[ProductRevenueItem] = Field(
        default_factory=list,
        description="主要產品項目清單，依 MOPS 表中出現順序排列。",
    )
    sales_return: Optional[int] = Field(
        None,
        description="減：銷貨退回及折讓金額（新台幣元）。若 MOPS 表無此欄位則為 null。",
    )
    total_revenue: Optional[int] = Field(
        None,
        description="合計業務營收淨額（新台幣元）。",
    )
    notes: Optional[str] = Field(
        None,
        description="附註訊息（例如「採用 IFRSs 後採自願申報，該公司無申報」）。",
    )
    error: Optional[str] = Field(None, description="MOPS 連線或解析錯誤訊息。")
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class OhlcvBar(BaseModel):
    """單一交易日的日 K bar（單位統一：金額 = 新台幣元，成交量 = 股）。"""
    model_config = ConfigDict(extra="allow")

    trade_date: str = Field(..., description="交易日（`YYYY-MM-DD`，西元年）。")
    stk_code: str = Field(..., description="股票代號。")
    open: Optional[float] = Field(None, description="開盤價。無成交時為 null。")
    high: Optional[float] = Field(None, description="盤中最高價。")
    low: Optional[float] = Field(None, description="盤中最低價。")
    close: Optional[float] = Field(None, description="收盤價。")
    volume: Optional[float] = Field(
        None,
        description="成交股數（單位：股）。TPEx 上游為「張」已 ×1000 對齊 TWSE。",
    )
    trade_value: Optional[float] = Field(
        None,
        description="成交金額（單位：新台幣元）。TPEx 上游為「仟元」已 ×1000 對齊 TWSE。",
    )
    transaction_count: Optional[float] = Field(None, description="成交筆數。")
    change: Optional[float] = Field(
        None,
        description="漲跌價差（相對前一交易日收盤，signed；上漲為正、下跌為負）。",
    )


class OhlcvResponse(BaseModel):
    """`GET /api/ohlcv` 回應。

    依 (股票, 日期範圍) 智能切換上游來源以最小化呼叫次數：

    * `range_days ≤ 7` → `per_day_market`：逐日拉全市場 payload，
      同一日多支股票查詢共享下載（backend cache 命中率高）。
    * `range_days > 7`  → `per_stock_month`：逐月拉單股整月日 K，
      一次 payload 涵蓋約 20 交易日、成本 ~1/20。

    上游 endpoint：
      * 上市 per-day：`https://www.twse.com.tw/exchangeReport/MI_INDEX`（Big5 CSV, ms950）
      * 上市 per-month：`https://www.twse.com.tw/exchangeReport/STOCK_DAY`
      * 上櫃 per-day：`https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes`
      * 上櫃 per-month：`https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock`
    """
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="是否有查到該股票的市場歸屬。false 表示 stk_code 不在基本資料表中。")
    stk_code: str = Field(..., description="查詢股票代號。")
    market: Optional[str] = Field(None, description="市場別：`上市` / `上櫃`；查無為 null。")
    from_date: str = Field(..., description="查詢起始日（`YYYY-MM-DD`，含）。")
    to_date: str = Field(..., description="查詢結束日（`YYYY-MM-DD`，含）。")
    strategy: str = Field(
        ...,
        description="採用的取樣策略：`per_day_market`（≤7 天）或 `per_stock_month`（>7 天）；查無市場時為空字串。",
    )
    rows: list[OhlcvBar] = Field(
        default_factory=list,
        description="日 K bar 陣列，依 `trade_date` 遞增。範圍內若無交易日，回傳空 array。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")


class InstitutionalNetBuySellRow(BaseModel):
    """單一交易日、單一股票的三大法人買賣超明細（單位：股）。

    * 上市來源：TWSE 「三大法人買賣超日報」T86（欄位順序 19 欄，直接對映）
    * 上櫃來源：TPEx 「三大法人買賣明細資訊」dailyTrade（欄位順序 24 欄，投信 / 自營商合計欄位位置與 TWSE 不同）

    * 「外資」以「不含外資自營商」為主口徑（跟 TWSE T86 主要對外揭露一致）。
    * `total_institutional_net_buy_sell` 為上游 payload 直接提供的合計欄位，**非本 backend 自行加總**。
    """
    model_config = ConfigDict(extra="allow")

    trade_date: str = Field(..., description="交易日（`YYYY-MM-DD`，西元年）。")
    stk_code: str = Field(..., description="股票代號。")
    stock_name: Optional[str] = Field(None, description="股票簡稱；以 `company_basic_info.short_name` 為主，退回 payload 上的名稱。")
    foreign_investors_net_buy_sell: Optional[int] = Field(
        None,
        description="外陸資買賣超股數（不含外資自營商）。上市取 T86 payload 第 5 欄；上櫃取 dailyTrade table[0] 第 5 欄。",
    )
    foreign_dealers_net_buy_sell: Optional[int] = Field(
        None,
        description="外資自營商買賣超股數。上市取 T86 payload 第 8 欄；上櫃取 dailyTrade table[0] 第 8 欄。",
    )
    investment_trust_net_buy_sell: Optional[int] = Field(
        None,
        description="投信買賣超股數。上市取 T86 payload 第 11 欄；上櫃取 dailyTrade table[0] 第 14 欄。",
    )
    dealers_net_buy_sell: Optional[int] = Field(
        None,
        description="自營商買賣超股數合計（自行買賣 + 避險）。上市取 T86 payload 第 12 欄；上櫃取 dailyTrade table[0] 第 23 欄。",
    )
    dealers_proprietary_net_buy_sell: Optional[int] = Field(
        None,
        description="自營商自行買賣買賣超股數。上市取 T86 第 15 欄；上櫃取 dailyTrade 第 17 欄。",
    )
    dealers_hedge_net_buy_sell: Optional[int] = Field(
        None,
        description="自營商避險買賣超股數。上市取 T86 第 18 欄；上櫃取 dailyTrade 第 20 欄。",
    )
    total_institutional_net_buy_sell: Optional[int] = Field(
        None,
        description="三大法人買賣超股數合計。**上游 payload 直接提供**，非本 backend 自行加總。上市取 T86 第 19 欄；上櫃取 dailyTrade 第 24 欄。",
    )


class InstitutionalNetBuySellResponse(BaseModel):
    """`GET /api/institutional-net-buy-sell` 回應。

    * 上游：
      * 上市：<https://www.twse.com.tw/rwd/zh/fund/T86> `?date=YYYYMMDD&selectType=ALL&response=json`
      * 上櫃：<https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade> `?date=YYYY/MM/DD&type=Daily&sect=EW&response=json`
    * 資料起始日：民國 101/5/2 = 2012-05-02。早於此日期不受理（回 400）。
    * 每日約收盤後 (T+0 傍晚) 更新。
    * 單位皆為「股」，本 endpoint 無單位轉換（上游本身即為股）。
    * 同一日多支股票查詢共享 backend 磁碟 cache `/tmp/institutional_cache/{market}/{YYYYMMDD}.json.gz`。
    """
    model_config = ConfigDict(extra="allow")

    found: bool = Field(..., description="stk_code 是否在 `company_basic_info` 中；false 表示未知股票。")
    stk_code: str = Field(..., description="查詢股票代號。")
    market: Optional[str] = Field(None, description="市場別：`上市` / `上櫃`；查無為 null。")
    trade_date: str = Field(..., description="查詢交易日（`YYYY-MM-DD`）。")
    row: Optional[InstitutionalNetBuySellRow] = Field(
        None,
        description="三大法人買賣超明細；當日全市場 payload 為空（非交易日、當日尚未收盤、或上游失敗）時為 null。",
    )
    source: Optional[str] = Field(None, description="本筆資料來源註記。")
