# db/poc — PoC SQL 說明

> 整體 db / pipeline 設計理念請見 [`db/README.md`](../README.md)。本目錄是其中 `poc` schema（研發層）的實作。

## 規則

1. 非 `_list` 結尾的 SQL **禁止** 使用 mutable 操作（例如 `::DATE`, `http_get`）。
2. `_list` 結尾的 SQL **不可** 呼叫 `http_get_content`。
3. 若需要 mutable 邏輯或型別轉換，請封裝為 immutable function 放在 `db/settings.sql`。
4. 每個 PoC SQL 對應一個 table/view。
5. `_list` 表把資料模型的實體（chain、company、quarter…）落到一張慢慢增長的表，下游 view 透過
incremental materialized view 建構，可以與 `_list` 表同步擴充資料，避免一次性大量發查 API。
6. 每張表的 rows 都必須是唯一的。
7. `_list.sql` 的欄位即為下游 view 的 primary key。
8. 沒有上游的 SQL 必須是 `_list.sql`

## 章節索引

1. [chain_list](#chain_list)
2. [raw_chain_info](#raw_chain_info)
3. [chain_info](#chain_info)
4. [company_list](#company_list)
5. [raw_product_revenue](#raw_product_revenue)
6. [product_revenue](#product_revenue)
7. [company_value_chain](#company_value_chain)
8. [raw_company_info](#raw_company_info)
9. [company_basic_info](#company_basic_info)
10. [company_business_items](#company_business_items)
11. [financial_year_list](#financial_year_list)
12. [raw_yearly_financials](#raw_yearly_financials)
13. [raw_yearly_financials_yfinance](#raw_yearly_financials_yfinance)
14. [financial_yearly_yfinance](#financial_yearly_yfinance)
15. [raw_monthly_revenue](#raw_monthly_revenue)
16. [monthly_revenue](#monthly_revenue)
17. [raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
18. [monthly_revenue_twse](#monthly_revenue_twse)
19. [raw_yearly_dividend](#raw_yearly_dividend)
20. [yearly_dividend](#yearly_dividend)
21. [raw_yearly_dividend_yfinance](#raw_yearly_dividend_yfinance)
22. [yearly_dividend_yfinance](#yearly_dividend_yfinance)
23. [financial_quarter_list](#financial_quarter_list)
24. [financial_quarterly](#financial_quarterly)

---

## chain_list

- **上游 SQL**：無（最上層 `_list`）
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/chains`
  - 上游網站：[櫃買中心 · 產業價值鏈資訊平台](https://ic.tpex.org.tw/)（沒有官方 JSON API；本服務在 `app/icchain.py::IC_CHAINS` 以常數提供）

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `ic_code` | TEXT | 產業鏈代碼（如 `D000` 半導體、`A300` 電動車、`5300` 人工智慧） | `chains[].ic_code` |
| `ic_name` | TEXT | 產業鏈中文名稱 | `chains[].ic_name` |

---

## raw_chain_info

- **上游 SQL**：[chain_list](#chain_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/chain/{ic_code}`
  - 上游網站：櫃買中心 · 產業價值鏈資訊平台 `https://ic.tpex.org.tw/introduce.php?ic={IC_CODE}`（server-rendered HTML，BeautifulSoup(lxml) 解析）
  - 首次任一查詢觸發背景拉 47 條鏈、落盤至 `data/icchain.json`（TTL 7 天）；僅保留 `<b>本國</b>` 區段

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `ic_code` | TEXT | 產業鏈代碼 | `chain_list.ic_code` |
| `ic_name` | TEXT | 產業鏈中文名稱 | `chain_list.ic_name` |
| `url` | TEXT | 本次呼叫的完整 URL（除錯用） | `'http://.../api/chain/' \|\| ic_code` |
| `segments` | JSONB | endpoint 回傳的整段 JSON（含上中下游樹、`top_code`、`sub_code`、各公司清單） | `custom.http_get_content(url)` |

---

## chain_info

- **上游 SQL**：[raw_chain_info](#raw_chain_info)
- **HTTP API endpoint**：無（純 JSONB 展開）

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `ic_code` | TEXT | 產業鏈代碼 | `segments.segments.ic_code` |
| `ic_name` | TEXT | 產業鏈中文名稱 | `segments.segments.ic_name` |
| `segment_key` | TEXT | 段位代號（`upstream` / `midstream` / `downstream`） | `segments.segments.segments` jsonb_each key |
| `top_code` | TEXT | 該段位下的「主分類」代碼 | `segments.segments.segments[*].top_code` |
| `top_name` | TEXT | 主分類中文名 | `segments.segments.segments[*].top_name` |
| `sub_code` | TEXT | 主分類下的「次分類」代碼 | `segments.segments.segments[*].sub_chains[*].sub_code` |
| `sub_name` | TEXT | 次分類中文名 | `segments.segments.segments[*].sub_chains[*].sub_name` |
| `stk_code` | TEXT | 該次分類下的公司股票代號 | `segments.segments.segments[*].sub_chains[*].companies[*].stk_code` |
| `company_name` | TEXT | 公司名稱 | `segments.segments.segments[*].sub_chains[*].companies[*].name` |

---

## company_list

- **上游 SQL**：[chain_info](#chain_info)
- **HTTP API endpoint**：無（純 SQL `DISTINCT`）

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號（例 `2330`） | `chain_info.stk_code` |
| `company_name` | TEXT | 公司名稱（產業鏈樹中的顯示名稱） | `chain_info.company_name` |

---

## raw_product_revenue

- **上游 SQL**：[company_list](#company_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/product-revenue`（無 `as_of`參數，回傳公司最後一次申報期）
  - 上游：[公開資訊觀測站 (MOPS) t05st08「各項產品業務營收統計表」](https://mops.twse.com.tw/mops/web/t05st08)。三步驟HTTP流程（描述見 `app/main.py` endpoint）

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_list.stk_code` |
| `product_revenue` | JSONB | `/product-revenue` endpoint 回傳整包 JSON（含 `year`, `month`, `company_name`, `items[]`） | `custom.http_get_content('.../product-revenue')` |

---

## product_revenue

- **上游 SQL**：[raw_product_revenue](#raw_product_revenue)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `ProductRevenueItem` + `ProductRevenueResponse`

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_product_revenue.stk_code` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `product_revenue.stock_id` |
| `company_name` | TEXT | 公司名稱（由 MOPS 表頭解析得到） | `product_revenue.company_name` |
| `year` | TEXT | 申報年度（民國年，字串，例 `113`） | `product_revenue.year` |
| `month` | TEXT | 申報月份（`MM`） | `product_revenue.month` |
| `rank` | TEXT | 產品序號標籤，例 `(1)`、`(2)`、`其他` | `product_revenue.items[].rank` |
| `name` | TEXT | 產品/業務項目名稱 | `product_revenue.items[].name` |
| `amount` | NUMERIC | 該項目營收金額（新台幣元；原始 HTML 為仟元，已乘以 1000） | `product_revenue.items[].amount` |
| `percentage` | NUMERIC | 該項目佔合計業務營收淨額 + 銷貨退回及折讓的百分比 (%) | `product_revenue.items[].percentage` |

---

## company_value_chain

- **上游 SQL**：[chain_info](#chain_info)
- **HTTP API endpoint**：無（純衍生 view，不額外呼叫 `/value-chain`）
- 設計理念：`/company/{id}/value-chain` 與 `/chain/{ic_code}` 共用同一份 `chain_tree` raw data，API 內部的 `company_index` 只是 `chain_tree` 的反向索引（純 dict 操作、無額外 HTTP）。因此公司視角的價值鏈完全可由 `chain_info` 衍生，符合規則 #2「不重複攤平同源資料」。
- 攤平顆粒度與 [chain_info](#chain_info) 相同；欄位 align（`ic_code` / `ic_name` / `segment_key` / `top_code` / `top_name` / `sub_code` / `sub_name` / `stk_code`），額外提供同 sub_chain 下的鄰居公司（self-join）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 公司股票代號（視角） | `chain_info.stk_code` |
| `ic_code` | TEXT | 產業鏈代碼 | `chain_info.ic_code` |
| `ic_name` | TEXT | 產業鏈中文名 | `chain_info.ic_name` |
| `segment_key` | TEXT | 段位代號（`upstream` / `midstream` / `downstream`） | `chain_info.segment_key` |
| `top_code` | TEXT | 主分類代碼 | `chain_info.top_code` |
| `top_name` | TEXT | 主分類中文名 | `chain_info.top_name` |
| `sub_code` | TEXT | 次分類代碼 | `chain_info.sub_code` |
| `sub_name` | TEXT | 次分類中文名 | `chain_info.sub_name` |
| `neighbor_stk_code` | TEXT | 同 sub_chain 下的鄰居公司股票代號 | `chain_info.stk_code`（self-join） |
| `neighbor_company_name` | TEXT | 鄰居公司名稱 | `chain_info.company_name`（self-join） |

---

## raw_company_info

- **上游 SQL**：[company_list](#company_list)
- **HTTP API endpoint**：兩個 endpoint 並列呼叫
  - `GET http://host.docker.internal:5002/api/company/{stock_id}/basic`
    - 上游：[證券交易所 (TWSE) OpenAPI](https://openapi.twse.com.tw/v1/opendata/t187ap03_L)（上市）+ [櫃買中心 (TPEx) OpenAPI](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O)（上櫃），合併並統一欄位、民國年日期轉 `YYYY-MM-DD`、行業代碼轉中文
  - `GET http://host.docker.internal:5002/api/company/{stock_id}/business-items`
    - 上游：[經濟部商工登記公示資料 (GCIS)](https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C)，以 `tax_id` OData filter 篩出 `Cmp_Business`，並去除「１．」「2.」等序號前綴

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_list.stk_code` |
| `company_name` | TEXT | 公司名稱（傳遞自上層） | `company_list.company_name` |
| `basic` | JSONB | `/basic` endpoint 回傳整包 JSON | `custom.http_get_content('.../basic')` |
| `business_items` | JSONB | `/business-items` endpoint 回傳整包 JSON（含 `categories[]`） | `custom.http_get_content('.../business-items')` |

---

## company_basic_info

- **上游 SQL**：[raw_company_info](#raw_company_info)
- **HTTP API endpoint**：無（純 JSON 攤平）

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號（傳遞自上層） | `raw_company_info.stk_code` |
| `company_name` | TEXT | 公司名稱（傳遞自上層） | `raw_company_info.company_name` |
| `market` | TEXT | 市場別（`上市` / `上櫃`） | `basic.market` |
| `tax_id` | TEXT | 統一編號 | `basic.tax_id` |
| `address` | TEXT | 公司地址 | `basic.address` |
| `website` | TEXT | 官網 URL | `basic.website` |
| `chairman` | TEXT | 董事長姓名 | `basic.chairman` |
| `stock_id` | TEXT | 股票代號（API 規範格式，與 `stk_code` 同步） | `basic.stock_id` |
| `short_name` | TEXT | 公司簡稱 | `basic.short_name` |
| `full_name` | TEXT | 公司全名 | `basic.company_name` |
| `english_name` | TEXT | 公司英文簡稱 | `basic.english_name` |
| `listing_date` | DATE | 上市/上櫃日（`YYYY-MM-DD`，經 `custom.parse_iso_date` 解析） | `basic.listing_date` |
| `industry_code` | TEXT | 產業分類代碼 | `basic.industry_code` |
| `industry_name` | TEXT | 產業分類中文名 | `basic.industry_name` |
| `general_manager` | TEXT | 總經理姓名 | `basic.general_manager` |
| `paid_in_capital` | BIGINT | 實收資本額（純數字才轉，否則 NULL） | `basic.paid_in_capital` |
| `incorporation_date` | DATE | 公司設立日（`YYYY-MM-DD`） | `basic.incorporation_date` |

> 註：原 SQL 中 `company_name, market, tax_id, ...` 第 4 行缺逗號（已知 bug），未來需修正才能正確編譯。

---

## company_business_items

- **上游 SQL**：[raw_company_info](#raw_company_info)
- **HTTP API endpoint**：無（純 JSON 攤平）

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stock_id` | TEXT | 股票代號 | `business_items.stock_id` |
| `code` | TEXT | 行業分類代碼（敘述條目時為 `null` / 空字串） | `business_items.categories[].code` |
| `desc` | TEXT | 該條業務敘述（已去除「１．」「2.」等前綴） | `business_items.categories[].desc` |

---

## financial_year_list

- **上游 SQL**：[company_basic_info](#company_basic_info)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 以 `custom.trunc_year(incorporation_date)` 為起點，`generate_series(..., CURRENT_DATE, INTERVAL '1 year')` 展開出每一個年初

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `year_start_date` | DATE | 公司歷年的 1 月 1 日（從設立年到今年） | `generate_series(trunc_year(incorporation_date), CURRENT_DATE, '1 year')` |

---

## raw_yearly_financials

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials?as_of={year_start_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockFinancialStatements`（免費 300 req/hr、無需 token）
  - 處理：抓近 5 年 quarterly rows，計算 `EPS / IncomeAfterTaxes / OperatingIncome / Revenue` 的 TTM；不足 4 季的指標回 null

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `financials` | JSONB | `/financials` endpoint 整包 JSON（含 `eps`, `net_income`, `operating_margin_pct`, `revenue_ttm_from_financial_statements`, `eps.ttm_quarters`, `net_income.ttm_quarters` …） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `year_start_date`） | `financial_year_list.year_start_date` |

---

## raw_yearly_financials_yfinance

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials/yfinance?as_of={year_start_date}`
  - 上游：yfinance Python Library（Yahoo Finance 非官方 wrapper）。與 FinMind 版同一輸出 spec，限流寬鬆許多、免 token；適合產 PoC 階段對比 / 其他 ad-hoc 研究。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `financials` | JSONB | `/financials/yfinance` endpoint 整包 JSON（結構與 raw_yearly_financials 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日 | `financial_year_list.year_start_date` |

---

## financial_yearly_yfinance

- **上游 SQL**：[raw_yearly_financials_yfinance](#raw_yearly_financials_yfinance)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [financial_quarterly](#financial_quarterly) align，只差在資料源 (yfinance vs FinMind) 以及以年度為關鍵 (vs financial_quarterly 以季為關鍵)

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_yearly_financials_yfinance.stk_code` |
| `as_of` | DATE | 本筆指標的查詢基準日 | `financials.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `financials.stock_id` |
| `eps_ttm` | NUMERIC | EPS TTM | `financials.eps.ttm` |
| `latest_quarter_date` | DATE | 最新可得的單季財報日 | `financials.eps.latest_quarter_date` |
| `latest_quarter_eps` | NUMERIC | 最新單季的 EPS | `financials.eps.latest_quarter_value` |
| `net_income_ttm` | NUMERIC | 稅後淨利 TTM | `financials.net_income.ttm` |
| `latest_quarter_net_income` | NUMERIC | 最新單季稅後淨利 | `financials.net_income.latest_quarter_value` |
| `operating_margin_pct` | NUMERIC | 營業利潤率 (%) | `financials.operating_margin_pct` |
| `revenue_ttm` | NUMERIC | 營收 TTM（取自財報） | `financials.revenue_ttm_from_financial_statements` |

---

## raw_monthly_revenue

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue?as_of={year_start_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockMonthRevenue`

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `revenue` | JSONB | `/revenue` endpoint 整包 JSON（含 `latest_month_label`, `latest_month_value`, `latest_month_yoy_pct`, `ttm_value`, `ttm_yoy_pct`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `year_start_date`） | `financial_year_list.year_start_date` |

---

## monthly_revenue

- **上游 SQL**：[raw_monthly_revenue](#raw_monthly_revenue)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `RevenueResponse`；主鍵顯示規則與 [financial_quarterly](#financial_quarterly) 同 (`stk_code` + `as_of`)

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_monthly_revenue.stk_code` |
| `as_of` | DATE | 本筆指標的查詢基準日 | `revenue.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `revenue.stock_id` |
| `latest_month_label` | TEXT | 最近一個月的年/月標籤（例 `2026/04`） | `revenue.latest_month_label` |
| `latest_month_value` | NUMERIC | 最近一個月營收（新台幣元） | `revenue.latest_month_value` |
| `latest_month_yoy_pct` | NUMERIC | 該月年增率 (%) | `revenue.latest_month_yoy_pct` |
| `ttm_value` | NUMERIC | 最近 12 個完整月份營收加總（TTM） | `revenue.ttm_value` |
| `ttm_yoy_pct` | NUMERIC | TTM 營收年增率 (%) | `revenue.ttm_yoy_pct` |

---

## raw_monthly_revenue_twse

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue/twse?as_of={year_start_date}`
  - 上游：證交所體系雙來源。「最新一個月」：[TWSE OpenAPI](https://openapi.twse.com.tw/v1/opendata/t187ap05_L) / [TPEx OpenAPI](https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv) t187ap05；「歷史月營收」：[公開資訊觀測站 MOPS](https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_113_5_0.html) t21sc03 採用 IFRSs 後每月營業收入彙總表。
  - 與 FinMind 版同一輸出 spec，提供「官方」源頭供審計溯源，免 token、免限流。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `revenue` | JSONB | `/revenue/twse` endpoint 整包 JSON（結構與 raw_monthly_revenue 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `year_start_date`） | `financial_year_list.year_start_date` |

---

## monthly_revenue_twse

- **上游 SQL**：[raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [monthly_revenue](#monthly_revenue) align，只差在資料源（TWSE/MOPS vs FinMind）

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_monthly_revenue_twse.stk_code` |
| `as_of` | DATE | 本筆指標的查詢基準日 | `revenue.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `revenue.stock_id` |
| `latest_month_label` | TEXT | 最近一個月的年/月標籤（例 `2026/04`） | `revenue.latest_month_label` |
| `latest_month_value` | NUMERIC | 最近一個月營收（新台幣元） | `revenue.latest_month_value` |
| `latest_month_yoy_pct` | NUMERIC | 該月年增率 (%) | `revenue.latest_month_yoy_pct` |
| `ttm_value` | NUMERIC | 最近 12 個完整月份營收加總（TTM） | `revenue.ttm_value` |
| `ttm_yoy_pct` | NUMERIC | TTM 營收年增率 (%) | `revenue.ttm_yoy_pct` |

---

## raw_yearly_dividend

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend?as_of={year_start_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockDividend`
  - 出身：各公司股利累計公告；依 `CashExDividendTradingDate` / `StockExDividendTradingDate` / `date` 順序選「除息日 ≤ as_of」最後一次

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `dividend` | JSONB | `/dividend` endpoint 整包 JSON（含 `dividend.{year, reference_date, cash_dividend, stock_dividend, cash_ex_dividend_date, cash_payment_date, stock_ex_dividend_date, announcement_date}`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `year_start_date`） | `financial_year_list.year_start_date` |

---

## yearly_dividend

- **上游 SQL**：[raw_yearly_dividend](#raw_yearly_dividend)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `DividendSection`；主鍵顯示規則與 [financial_quarterly](#financial_quarterly) 同 (`stk_code` + `as_of`)
- WHERE 僅取 `dividend != null` (即 endpoint 有找到股利的 row)

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_yearly_dividend.stk_code` |
| `as_of` | DATE | 本筆股利的查詢基準日 | `dividend.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `dividend.stock_id` |
| `dividend_year` | TEXT | 股利所屬年度 (FinMind 原始 `year`) | `dividend.dividend.year` |
| `reference_date` | DATE | 本服務挑選使用的「除息日 ≤ as_of」基準日 | `dividend.dividend.reference_date` |
| `cash_dividend` | NUMERIC | 每股現金股利 (元) | `dividend.dividend.cash_dividend` |
| `stock_dividend` | NUMERIC | 每股股票股利 (元) | `dividend.dividend.stock_dividend` |
| `cash_ex_dividend_date` | DATE | 現金股利除息交易日 | `dividend.dividend.cash_ex_dividend_date` |
| `cash_payment_date` | DATE | 現金股利發放日 | `dividend.dividend.cash_payment_date` |
| `stock_ex_dividend_date` | DATE | 股票股利除權交易日 | `dividend.dividend.stock_ex_dividend_date` |
| `announcement_date` | DATE | 股利公告日 | `dividend.dividend.announcement_date` |

---

## raw_yearly_dividend_yfinance

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/yfinance?as_of={year_start_date}`
  - 上游：yfinance Python Library (Yahoo Finance) — `ticker.dividends`
  - 與 FinMind 版同一輸出 spec；yfinance 不提供股票股利 / 公告日 / 現金股利發放日（對應欄位為 null / 0）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `dividend` | JSONB | `/dividend/yfinance` endpoint 整包 JSON（結構與 raw_yearly_dividend 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `year_start_date`） | `financial_year_list.year_start_date` |

---

## yearly_dividend_yfinance

- **上游 SQL**：[raw_yearly_dividend_yfinance](#raw_yearly_dividend_yfinance)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [yearly_dividend](#yearly_dividend) align，只差在資料源（yfinance vs FinMind）。`stock_dividend` / `cash_payment_date` / `stock_ex_dividend_date` / `announcement_date` 上游 endpoint 已回 null/0，此處保留同 schema。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_yearly_dividend_yfinance.stk_code` |
| `as_of` | DATE | 本筆股利的查詢基準日 | `dividend.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `dividend.stock_id` |
| `dividend_year` | TEXT | 股利所屬年度 | `dividend.dividend.year` |
| `reference_date` | DATE | 「除息日 ≤ as_of」基準日 | `dividend.dividend.reference_date` |
| `cash_dividend` | NUMERIC | 每股現金股利 (元) | `dividend.dividend.cash_dividend` |
| `stock_dividend` | NUMERIC | 每股股票股利 (yfinance 不提供，為 0) | `dividend.dividend.stock_dividend` |
| `cash_ex_dividend_date` | DATE | 現金股利除息交易日 | `dividend.dividend.cash_ex_dividend_date` |
| `cash_payment_date` | DATE | 現金股利發放日 (yfinance 不提供，為 null) | `dividend.dividend.cash_payment_date` |
| `stock_ex_dividend_date` | DATE | 股票股利除權交易日 (yfinance 不提供，為 null) | `dividend.dividend.stock_ex_dividend_date` |
| `announcement_date` | DATE | 股利公告日 (yfinance 不提供，為 null) | `dividend.dividend.announcement_date` |

---

## financial_quarter_list

- **上游 SQL**：[raw_yearly_financials](#raw_yearly_financials)
- **HTTP API endpoint**：無（純 JSONB 展開）

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_yearly_financials.stk_code` |
| `quater` | DATE | 該季最後一日（如 `2024-12-31`、`2024-09-30`） | `financials.eps.ttm_quarters[] ∪ financials.net_income.ttm_quarters[]` |

> 註：欄位名 `quater` 保留現有拼字（原 SQL 即如此），未來如需改為 `quarter` 請同步調整下游。

---

## financial_quarterly

- **上游 SQL**：[financial_quarter_list](#financial_quarter_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials?as_of={quarter}`
  - 上游：FinMind v4 `TaiwanStockFinancialStatements`

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_quarter_list.stk_code` |
| `as_of` | DATE | 本筆指標的查詢基準日（傳入的季別） | `financials.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `financials.stock_id` |
| `eps_ttm` | NUMERIC | EPS TTM（最近 4 季 EPS 加總；不足 4 季 null） | `financials.eps.ttm` |
| `latest_quarter_date` | DATE | 最新可得的單季財報日 | `financials.eps.latest_quarter_date` |
| `latest_quarter_eps` | NUMERIC | 最新單季的 EPS 值 | `financials.eps.latest_quarter_value` |
| `net_income_ttm` | NUMERIC | 稅後淨利 TTM | `financials.net_income.ttm` |
| `latest_quarter_net_income` | NUMERIC | 最新單季稅後淨利 | `financials.net_income.latest_quarter_value` |
| `operating_margin_pct` | NUMERIC | 營業利潤率（%）= `OperatingIncome(TTM) / Revenue(TTM) × 100` | `financials.operating_margin_pct` |
| `revenue_ttm` | NUMERIC | 營收 TTM（取自財報；月營收 TTM 請另用 `/api/company/{id}/revenue`） | `financials.revenue_ttm_from_financial_statements` |

> 篩選：僅保留 `financials.eps.ttm <> null` 的 row（過濾掉 FinMind 不足 4 季的舊年代）。
> 注意：本檔在 view 內呼叫 `custom.http_get_content`，與規則 #1 有衝突，未來建議改為 `raw_financial_quarterly` + 衍生 view 兩段式。
