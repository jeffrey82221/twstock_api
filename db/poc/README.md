# db/poc — PoC SQL 說明

> 整體 db / pipeline 設計理念請見 [`db/README.md`](../README.md)。本目錄是其中 `poc` schema（研發層）的實作。

## 規則

1. 非 `_list` 結尾的 SQL **禁止** 使用 mutable 操作（例如 `::DATE`, `http_get`）。
2. `_list` 結尾的 SQL **不可** 呼叫 `http_get_content`。
3. 若需要 mutable 邏輯或型別轉換,請封裝為 immutable function 放在 `db/settings.sql`。
4. 每個 PoC SQL 對應一個 table/view。
5. `_list` 表把資料模型的實體（chain、company、quarter…）落到一張慢慢增長的表,下游 view 透過
incremental materialized view 建構,可以與 `_list` 表同步擴充資料,避免一次性大量發查 API。
6. 每張表的 rows 都必須是唯一的。
7. `_list.sql` 的欄位即為下游 view 的 primary key。
8. 沒有上游的 SQL 必須是 `_list.sql`，檢查 .sql 裡面有沒有 {{ schema }} 來判斷是否有上游
9. `raw_` 開頭的SQL用途是抓取 app/main.py 的 endpoint API return 結果
10. 非 `raw_`, `_list.sql` 的 sql 目的是做 json 內容的欄位正規化,請以資料可用性來決定如何整理資料
11. `raw_` 開頭的SQL,需搭配一個 `_list.sql`作為上游,`_list.sql` 的邏輯要能指定 endpoint 的 API calling 可以遍歷所有 endpoint function 可能的 input ,但請避免重複的結果抓取。(e.g., 雖然「月營收」可以以日為單位去 call,但沒有必要在時間軸上這麼密集的抓取資料,最好只針對公佈日去抓取資料)
12. 請把民國年月日整合成西元年月日（以DATE來存）
13. 避免使用 WHERE 條件,讓 POC 階段會 full scan 整個上游表,應透過對 endpoint 的資料源的理解,從 相關 endpoint 找出資料邊界,融入到 `_list.sql` 表的邏輯中,讓下游的 `raw_` 表資料爬取可以有合理的起始點。
    - **例外**：`_list.sql` 允許 `WHERE <field> IS NOT NULL` 過濾「不可用事件」（例：除息日為 NULL 的歷史列）,因為此類列對下游 `as_of` API 呼叫無意義。這屬資料品質層清理,不算隱藏 raw 母體邊界。
14. 不同時間維度的資料,如 monthly, yearly, quarterly 資料,應基於不同的 `_list.sql` 來設定母體範圍
15. **事件性資料應建立事件層 endpoint 作為 `_list` 母體。** 當資料本質為離散事件（股利除息、產品營收自願申報）而非連續時間序列時：
    - **禁止**用規則性時間格點（每月月首、每年年初）作為 `_list` 母體,因為 as_of endpoint 只會回「該日期前最後一筆」,對格點採樣會產生大量重複命中的舊事件,浪費 API 也讓下游看到冗餘 rows。
    - 應在 `app/main.py` 新增「事件母體」endpoint（例：`/dividend/history` 回整段除息歷史 events array、`/product-revenue/filers?ym=&market=` 回該月該市場真正申報的公司清單）。
    - `_list.sql` 透過該事件母體 endpoint 產出「實際有事件的 (stk_code, event_date)」,下游 `raw_*` 只對真正有事件的 (id, date) 呼叫 as_of endpoint。
    - Rule 11 的「避免重複」本質是「不對沒事件的日期打 API」;規則性格點只是規則 15 的特例（月營收每月都公佈,格點恰等於事件母體）。
16. 避免使用 outter join, left join，因為 pg_ivm 只支援 inner join

## 章節索引

1. [chain_list](#chain_list)
2. [raw_chain_info](#raw_chain_info)
3. [chain_info](#chain_info)
4. [company_list](#company_list)
5. [product_revenue_filer_scope](#product_revenue_filer_scope)
6. [raw_product_revenue_filers](#raw_product_revenue_filers)
7. [product_revenue_filer_list](#product_revenue_filer_list)
8. [raw_product_revenue](#raw_product_revenue)
9. [product_revenue](#product_revenue)
10. [raw_company_info](#raw_company_info)
11. [company_basic_info](#company_basic_info)
12. [company_business_items](#company_business_items)
13. [financial_year_list](#financial_year_list)
14. [raw_yearly_financials](#raw_yearly_financials)
15. [raw_yearly_financials_yfinance](#raw_yearly_financials_yfinance)
16. [financial_yearly_yfinance](#financial_yearly_yfinance)
17. [financial_month_list](#financial_month_list)
18. [raw_monthly_revenue](#raw_monthly_revenue)
19. [monthly_revenue](#monthly_revenue)
20. [raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
21. [monthly_revenue_twse](#monthly_revenue_twse)
22. [raw_dividend_history](#raw_dividend_history)
23. [raw_dividend_history_yfinance](#raw_dividend_history_yfinance)
24. [dividend_event_list](#dividend_event_list)
25. [dividend_event_list_yfinance](#dividend_event_list_yfinance)
26. [raw_dividend](#raw_dividend)
27. [dividend](#dividend)
28. [raw_dividend_yfinance](#raw_dividend_yfinance)
29. [dividend_yfinance](#dividend_yfinance)
30. [financial_quarter_list](#financial_quarter_list)
31. [financial_quarterly](#financial_quarterly)
32. [financial_quarter_yfinance_list](#financial_quarter_yfinance_list)
33. [raw_quarterly_financials_yfinance](#raw_quarterly_financials_yfinance)
34. [financial_quarterly_yfinance](#financial_quarterly_yfinance)

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
  - 上游網站：櫃買中心 · 產業價值鏈資訊平台 `https://ic.tpex.org.tw/introduce.php?ic={IC_CODE}`（server-rendered HTML,BeautifulSoup(lxml) 解析）
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

## product_revenue_filer_scope

- **上游 SQL**：無外部（純 SQL `generate_series` + `VALUES` 產生時間邊界 × 市場）
- **HTTP API endpoint**：無（非 `_list`,亦不呼叫 HTTP）
- **設計理念（rule 15 支援）**：PoC 階段預設「近 5 年」的 MOPS 產品營收全公司掃描範圍。若未來要擴大到完整歷史,只需修改本檔的 `INTERVAL '5 years'`,此為**唯一時間邊界調整點**,下游 `raw_product_revenue_filers` / `product_revenue_filer_list` / `raw_product_revenue` / `product_revenue` 均自動跟隨擴大。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `ym` | TEXT | 民國年月 5 碼字串（例 `'11312'` = 民國 113 年 12 月） | `generate_series(now - 5y, now, '1 month')` 民國化 |
| `market` | TEXT | 市場別（`'sii'` = 上市 / `'otc'` = 上櫃） | `VALUES ('sii'), ('otc')` |

---

## raw_product_revenue_filers

- **上游 SQL**：[product_revenue_filer_scope](#product_revenue_filer_scope)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/product-revenue/filers?ym={ym}&market={market}`
  - 上游資料源：[公開資訊觀測站 (MOPS) ajax_t05st08_all](https://mops.twse.com.tw/mops/web/ajax_t05st08_all)（該月該市場所有申報「各項產品業務營收」的公司清單）
- 設計理念（rule 15）：MOPS 各項產品業務營收 IFRS 後改自願申報,非每公司每月都有申報。本 raw 對每 `(ym, market)` 打一次 filers endpoint,取得「真正有申報的 co_id 陣列」,供下游 `product_revenue_filer_list` 攤平為 (co_id × ym) 事件母體。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `ym` | TEXT | 民國年月 5 碼字串 | `product_revenue_filer_scope.ym` |
| `market` | TEXT | 市場別 | `product_revenue_filer_scope.market` |
| `filers` | JSONB | filers endpoint 回傳整包 JSON（含 `co_ids[]`, `ym`, `market`, `total_count`） | `custom.http_get_content(url)` |

---

## product_revenue_filer_list

- **上游 SQL**：[raw_product_revenue_filers](#raw_product_revenue_filers)
- **HTTP API endpoint**：無（`_list` 不呼叫 HTTP,rule 2）
- 設計理念（rule 15）：product_revenue 屬「事件性資料」— 每公司只在自己申報的月份才有明細。事件母體由 MOPS 該月申報清單決定,非以規則性格點採樣。
- 設計理念（rule 12）：民國年月字串 ym（例 `'11312'`）→ 西元 `DATE` report_month（`2024-12-01`）。
- 設計理念（rule 6）：以 `(stk_code, report_month)` 為唯一 key(同一公司同月僅一次申報)。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號（`filers.co_ids[]` 展開） | `raw_product_revenue_filers.filers.co_ids[]` |
| `ym` | TEXT | 民國年月 5 碼字串 | `raw_product_revenue_filers.ym` |
| `report_month` | DATE | 申報年月（西元,該月 1 日） | `ym` 民國→西元 |

---

## raw_product_revenue

- **上游 SQL**：[product_revenue_filer_list](#product_revenue_filer_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/product-revenue?as_of={report_month}`
  - 上游：[公開資訊觀測站 (MOPS) t05st08「各項產品業務營收統計表」](https://mops.twse.com.tw/mops/web/t05st08)。三步驟 HTTP 流程（描述見 `app/main.py` endpoint）。
- 設計理念（rule 15）：以事件母體 `product_revenue_filer_list` 每列（實際有申報的月份）觸發一次 as_of 呼叫,非笛卡兒積遍歷所有 (公司 × 月)。
- 設計理念（rule 1）：as_of 使用該月月首（`custom.date_to_iso(report_month)`）而非月末,因 `INTERVAL` 位移轉 `DATE` 需要 STABLE cast，違反 IMMUTABLE。MOPS 同月申報 as_of 落在月首或月末都會鎖到同一次申報。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `product_revenue_filer_list.stk_code` |
| `ym` | TEXT | 民國年月 5 碼字串 | `product_revenue_filer_list.ym` |
| `report_month` | DATE | 申報年月（西元,該月 1 日） | `product_revenue_filer_list.report_month` |
| `product_revenue` | JSONB | `/product-revenue?as_of=X` endpoint 回傳整包 JSON（含 `year`, `month`, `company_name`, `items[]`） | `custom.http_get_content(url)` |

---

## product_revenue

- **上游 SQL**：[raw_product_revenue](#raw_product_revenue)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `ProductRevenueItem` + `ProductRevenueResponse`。
- 設計理念（rule 15）：raw 表已由事件母體驅動,每列 `(stk_code, report_month)` 都是真實有申報的事件;不需從 JSON 民國年月重新合成 `report_month`。
- 設計理念（rule 13）：不做 WHERE 過濾,以 LEFT JOIN LATERAL 攤平,保留 `items=null` / 空 array 的公司列（產品欄位為 NULL）。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_product_revenue.stk_code` |
| `ym` | TEXT | 民國年月 5 碼字串 | `raw_product_revenue.ym` |
| `report_month` | DATE | 申報年月（西元,該月 1 日） | `raw_product_revenue.report_month` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `product_revenue.stock_id` |
| `company_name` | TEXT | 公司名稱（由 MOPS 表頭解析得到） | `product_revenue.company_name` |
| `sales_return` | NUMERIC | 減：銷貨退回及折讓金額（新台幣元） | `product_revenue.sales_return` |
| `total_revenue` | NUMERIC | 合計業務營收淨額（新台幣元） | `product_revenue.total_revenue` |
| `rank` | TEXT | 產品序號標籤,例 `(1)`、`(2)`、`其他` | `product_revenue.items[].rank` |
| `name` | TEXT | 產品/業務項目名稱 | `product_revenue.items[].name` |
| `amount` | NUMERIC | 該項目營收金額（新台幣元；原始 HTML 為仟元,已乘以 1000） | `product_revenue.items[].amount` |
| `percentage` | NUMERIC | 該項目佔合計業務營收淨額 + 銷貨退回及折讓的百分比 (%) | `product_revenue.items[].percentage` |

---

## raw_company_info

- **上游 SQL**：[company_list](#company_list)
- **HTTP API endpoint**：兩個 endpoint 並列呼叫
  - `GET http://host.docker.internal:5002/api/company/{stock_id}/basic`
    - 上游：[證券交易所 (TWSE) OpenAPI](https://openapi.twse.com.tw/v1/opendata/t187ap03_L)（上市）+ [櫃買中心 (TPEx) OpenAPI](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O)（上櫃）,合併並統一欄位、民國年日期轉 `YYYY-MM-DD`、行業代碼轉中文
  - `GET http://host.docker.internal:5002/api/company/{stock_id}/business-items`
    - 上游：[經濟部商工登記公示資料 (GCIS)](https://data.gcis.nat.gov.tw/od/data/api/236EE382-4942-41A9-BD03-CA0709025E7C),以 `tax_id` OData filter 篩出 `Cmp_Business`,並去除「１．」「2.」等序號前綴

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
| `stock_id` | TEXT | 股票代號（API 規範格式,與 `stk_code` 同步） | `basic.stock_id` |
| `short_name` | TEXT | 公司簡稱 | `basic.short_name` |
| `full_name` | TEXT | 公司全名 | `basic.company_name` |
| `english_name` | TEXT | 公司英文簡稱 | `basic.english_name` |
| `listing_date` | DATE | 上市/上櫃日（`YYYY-MM-DD`,經 `custom.parse_iso_date` 解析） | `basic.listing_date` |
| `industry_code` | TEXT | 產業分類代碼 | `basic.industry_code` |
| `industry_name` | TEXT | 產業分類中文名 | `basic.industry_name` |
| `general_manager` | TEXT | 總經理姓名 | `basic.general_manager` |
| `paid_in_capital` | BIGINT | 實收資本額（純數字才轉,否則 NULL） | `basic.paid_in_capital` |
| `incorporation_date` | DATE | 公司設立日（`YYYY-MM-DD`） | `basic.incorporation_date` |

> 註：原 SQL 中 `company_name, market, tax_id, ...` 第 4 行缺逗號（已知 bug）,未來需修正才能正確編譯。

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
- 以 `custom.trunc_year(incorporation_date)` 為起點,`generate_series(..., CURRENT_DATE, INTERVAL '1 year')` 展開出每一個年初

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `year_start_date` | DATE | 公司歷年的 1 月 1 日（從設立年到今年） | `generate_series(trunc_year(incorporation_date), CURRENT_DATE, '1 year')` |

---

## raw_yearly_financials

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials?as_of={year_start_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockFinancialStatements`（免費 300 req/hr、無需 token）
  - 處理：抓近 5 年 quarterly rows,計算 `EPS / IncomeAfterTaxes / OperatingIncome / Revenue` 的 TTM;不足 4 季的指標回 null

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `financials` | JSONB | `/financials` endpoint 整包 JSON（含 `eps`, `net_income`, `operating_margin_pct`, `revenue_ttm_from_financial_statements`, `eps.ttm_quarters`, `net_income.ttm_quarters` …） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `year_start_date`） | `financial_year_list.year_start_date` |

---

## raw_yearly_financials_yfinance

- **上游 SQL**：[financial_year_list](#financial_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials/yfinance?as_of={year_start_date}`
  - 上游：yfinance Python Library（Yahoo Finance 非官方 wrapper）。與 FinMind 版同一輸出 spec,限流寬鬆許多、免 token;適合產 PoC 階段對比 / 其他 ad-hoc 研究。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_list.stk_code` |
| `financials` | JSONB | `/financials/yfinance` endpoint 整包 JSON（結構與 raw_yearly_financials 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日 | `financial_year_list.year_start_date` |

---

## financial_yearly_yfinance

- **上游 SQL**：[raw_yearly_financials_yfinance](#raw_yearly_financials_yfinance)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [financial_quarterly](#financial_quarterly)、[financial_quarterly_yfinance](#financial_quarterly_yfinance) align,只差在資料源 (yfinance vs FinMind) 以及以年度為關鍵。
- 設計理念（規則 13）：不做 WHERE 過濾,保留 raw 母體的所有 rows。
- 型別安全：所有 numeric 欄位一律走 `->>` (回傳 text) 再 `::NUMERIC`,jsonb null 會安全轉為 SQL NULL,避免 `cannot cast jsonb null to type numeric`。

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

## financial_month_list

- **上游 SQL**：[company_basic_info](#company_basic_info)
- **HTTP API endpoint**：無（純 SQL 產生）
- 設計理念（規則 11, 14, 15）：月營收公布頻率為每月一次（次月 10 日前）,幾乎所有公司每月都有,規則性格點恰等於事件母體 — 為 rule 15 的退化特例。因此仍以「每月一次」的 as_of 遍歷所有月份切片,不必以日為單位密集抓取。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `month_start_date` | DATE | 從公司成立日所屬月份開始、每月 1 日 generate 一列,直到 CURRENT_DATE | `generate_series(...)` |

---

## raw_monthly_revenue

- **上游 SQL**：[financial_month_list](#financial_month_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue?as_of={month_start_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockMonthRevenue`
- 設計理念（規則 14）：月頻資料須有專屬 `_list`（financial_month_list）,不與年頻 dividend / financials 混用同一個 `financial_year_list`。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_month_list.stk_code` |
| `revenue` | JSONB | `/revenue` endpoint 整包 JSON（含 `latest_month_label`, `latest_month_value`, `latest_month_yoy_pct`, `ttm_value`, `ttm_yoy_pct`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `month_start_date`） | `financial_month_list.month_start_date` |

---

## monthly_revenue

- **上游 SQL**：[raw_monthly_revenue](#raw_monthly_revenue)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `RevenueResponse`;主鍵顯示規則與 [financial_quarterly](#financial_quarterly) 同 (`stk_code` + `as_of`)
- 設計理念（規則 13）：不做 WHERE 過濾,保留 raw 母體的所有 rows（含 found=false / 值為 null 的列）。

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

- **上游 SQL**：[financial_month_list](#financial_month_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue/twse?as_of={month_start_date}`
  - 上游：證交所體系雙來源。「最新一個月」：[TWSE OpenAPI](https://openapi.twse.com.tw/v1/opendata/t187ap05_L) / [TPEx OpenAPI](https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv) t187ap05;「歷史月營收」：[公開資訊觀測站 MOPS](https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_113_5_0.html) t21sc03 採用 IFRSs 後每月營業收入彙總表。
  - 與 FinMind 版同一輸出 spec,提供「官方」源頭供審計溯源,免 token、免限流。
- 設計理念（規則 14）：與 `raw_monthly_revenue` 共用 `financial_month_list`,避免重複建 `_list`。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_month_list.stk_code` |
| `revenue` | JSONB | `/revenue/twse` endpoint 整包 JSON（結構與 raw_monthly_revenue 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `month_start_date`） | `financial_month_list.month_start_date` |

---

## monthly_revenue_twse

- **上游 SQL**：[raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [monthly_revenue](#monthly_revenue) align,只差在資料源（TWSE/MOPS vs FinMind）
- 設計理念（規則 13）：不做 WHERE 過濾,保留 raw 母體的所有 rows。

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

## raw_dividend_history

- **上游 SQL**：[company_list](#company_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/history`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockDividend`（20 年區間全量）
- 設計理念（rule 15）：股利是「事件性資料」— 每公司每年 0~數次除息事件。對 (公司 × 每年年初) 打 `/dividend?as_of=X` 只會回 as_of 前最後一筆,多年查詢會產生大量重複命中。本 raw 每公司一次撈整包歷史 events array,作為下游 `dividend_event_list` 的來源母體。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_list.stk_code` |
| `dividend_history` | JSONB | `/dividend/history` endpoint 整包 JSON（含 `stock_id`, `total_events`, `events[]`）;每個 event 為 `DividendSection` 結構 | `custom.http_get_content(url)` |

---

## raw_dividend_history_yfinance

- **上游 SQL**：[company_list](#company_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/history/yfinance`
  - 上游：yfinance Python Library — `Ticker.dividends`（整段歷史）
- 與 `raw_dividend_history` (FinMind 版) 結構完全一致,純資料源替代版。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_list.stk_code` |
| `dividend_history` | JSONB | `/dividend/history/yfinance` endpoint 整包 JSON（結構與 raw_dividend_history 一致） | `custom.http_get_content(url)` |

---

## dividend_event_list

- **上游 SQL**：[raw_dividend_history](#raw_dividend_history)
- **HTTP API endpoint**：無（`_list` 不呼叫 HTTP,rule 2）
- 用途：作為 `raw_dividend` 的上游母體 — 攤平該公司歷史所有除息日 `cash_ex_dividend_date`。
- 設計理念（rule 15）：股利屬「事件性資料」,事件母體由資料本身告訴我們（哪些日期真正有除息）,而非以規則性時間格點採樣。
- 設計理念（rule 13 例外）：本檔以 `WHERE cash_ex_dividend_date IS NOT NULL` 過濾「不可用事件」（FinMind 罕見狀況：僅有股票股利 / 除權日,無現金除息日）;這些列對下游 as_of endpoint 無意義,屬資料品質層清理。
- 設計理念（rule 6）：以 `(stk_code, cash_ex_dividend_date)` 為唯一 key（`DISTINCT` 去重罕見 FinMind 同日重複）。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend_history.stk_code` |
| `cash_ex_dividend_date` | DATE | 現金股利除息交易日（本次事件的 as_of） | `dividend_history.events[].cash_ex_dividend_date` |

---

## dividend_event_list_yfinance

- **上游 SQL**：[raw_dividend_history_yfinance](#raw_dividend_history_yfinance)
- **HTTP API endpoint**：無
- 與 [dividend_event_list](#dividend_event_list) 同結構、同意圖;差別在資料源。用途：作為 `raw_dividend_yfinance` 的上游母體。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend_history_yfinance.stk_code` |
| `cash_ex_dividend_date` | DATE | 現金股利除息交易日 | `dividend_history.events[].cash_ex_dividend_date` |

---

## raw_dividend

- **上游 SQL**：[dividend_event_list](#dividend_event_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend?as_of={cash_ex_dividend_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockDividend`（透過 `_pick_dividend` 取 as_of 前最後一筆）
- 設計理念（rule 15）：以事件母體「該公司歷史真正的除息日」作為 as_of 遍歷。每筆 `(stk_code, ex_date)` 打一次 `/dividend?as_of=ex_date`,每次命中的就是「該次除息事件對應的股利明細」— 不會落到重複的 last_dividend,也不會浪費 API 打沒事件的日期。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `dividend_event_list.stk_code` |
| `dividend` | JSONB | `/dividend?as_of=X` endpoint 整包 JSON（含 `dividend.{year, reference_date, cash_dividend, stock_dividend, cash_ex_dividend_date, cash_payment_date, stock_ex_dividend_date, announcement_date}`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `cash_ex_dividend_date`） | `dividend_event_list.cash_ex_dividend_date` |

---

## dividend

- **上游 SQL**：[raw_dividend](#raw_dividend)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `DividendSection`;主鍵顯示規則與 [financial_quarterly](#financial_quarterly) 同 (`stk_code` + `as_of`)。每列 = 該公司歷史某次除息事件對應的股利明細。
- 設計理念（rule 15）：每列對應歷史一次真實除息事件（by `cash_ex_dividend_date`）,非規則性時間格點採樣。
- 設計理念（rule 13）：不做 WHERE 過濾,保留 raw 母體所有 rows。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend.stk_code` |
| `as_of` | DATE | 本筆股利事件的除息日（= raw 表 as_of） | `dividend.as_of` |
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

## raw_dividend_yfinance

- **上游 SQL**：[dividend_event_list_yfinance](#dividend_event_list_yfinance)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/yfinance?as_of={cash_ex_dividend_date}`
  - 上游：yfinance Python Library — `Ticker.dividends`
- 與 `raw_dividend` (FinMind 版) 結構完全一致,純資料源替代版。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `dividend_event_list_yfinance.stk_code` |
| `dividend` | JSONB | `/dividend/yfinance?as_of=X` endpoint 整包 JSON（結構與 raw_dividend 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `cash_ex_dividend_date`） | `dividend_event_list_yfinance.cash_ex_dividend_date` |

---

## dividend_yfinance

- **上游 SQL**：[raw_dividend_yfinance](#raw_dividend_yfinance)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [dividend](#dividend) align,只差在資料源（yfinance vs FinMind）。`stock_dividend` / `cash_payment_date` / `stock_ex_dividend_date` / `announcement_date` 上游 endpoint 已回 null/0,此處保留同 schema。
- 設計理念（rule 15）：每列對應歷史一次真實除息事件（by `cash_ex_dividend_date`）。
- 設計理念（rule 13）：不做 WHERE 過濾,保留 raw 母體所有 rows。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend_yfinance.stk_code` |
| `as_of` | DATE | 本筆股利事件的除息日 | `dividend.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `dividend.stock_id` |
| `dividend_year` | TEXT | 股利所屬年度 | `dividend.dividend.year` |
| `reference_date` | DATE | 「除息日 ≤ as_of」基準日 | `dividend.dividend.reference_date` |
| `cash_dividend` | NUMERIC | 每股現金股利 (元) | `dividend.dividend.cash_dividend` |
| `stock_dividend` | NUMERIC | 每股股票股利 (yfinance 不提供,為 0) | `dividend.dividend.stock_dividend` |
| `cash_ex_dividend_date` | DATE | 現金股利除息交易日 | `dividend.dividend.cash_ex_dividend_date` |
| `cash_payment_date` | DATE | 現金股利發放日 (yfinance 不提供,為 null) | `dividend.dividend.cash_payment_date` |
| `stock_ex_dividend_date` | DATE | 股票股利除權交易日 (yfinance 不提供,為 null) | `dividend.dividend.stock_ex_dividend_date` |
| `announcement_date` | DATE | 股利公告日 (yfinance 不提供,為 null) | `dividend.dividend.announcement_date` |

---

## financial_quarter_list

- **上游 SQL**：[raw_yearly_financials](#raw_yearly_financials)
- **HTTP API endpoint**：無（純 JSONB 展開）

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_yearly_financials.stk_code` |
| `quater` | DATE | 該季最後一日（如 `2024-12-31`、`2024-09-30`） | `financials.eps.ttm_quarters[] ∪ financials.net_income.ttm_quarters[]` |

> 註：欄位名 `quater` 保留現有拼字（原 SQL 即如此）,未來如需改為 `quarter` 請同步調整下游。

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
| `eps_ttm` | NUMERIC | EPS TTM（最近 4 季 EPS 加總;不足 4 季 null） | `financials.eps.ttm` |
| `latest_quarter_date` | DATE | 最新可得的單季財報日 | `financials.eps.latest_quarter_date` |
| `latest_quarter_eps` | NUMERIC | 最新單季的 EPS 值 | `financials.eps.latest_quarter_value` |
| `net_income_ttm` | NUMERIC | 稅後淨利 TTM | `financials.net_income.ttm` |
| `latest_quarter_net_income` | NUMERIC | 最新單季稅後淨利 | `financials.net_income.latest_quarter_value` |
| `operating_margin_pct` | NUMERIC | 營業利潤率（%）= `OperatingIncome(TTM) / Revenue(TTM) × 100` | `financials.operating_margin_pct` |
| `revenue_ttm` | NUMERIC | 營收 TTM（取自財報;月營收 TTM 請另用 `/api/company/{id}/revenue`） | `financials.revenue_ttm_from_financial_statements` |

> 篩選：僅保留 `financials.eps.ttm <> null` 的 row（過濾掉 FinMind 不足 4 季的舊年代）。
> 注意：本檔在 view 內呼叫 `custom.http_get_content`,與規則 #1 有衝突,未來建議改為 `raw_financial_quarterly` + 衍生 view 兩段式。yfinance 版已完成拆分,請見下方 `raw_quarterly_financials_yfinance` + `financial_quarterly_yfinance`。

---

## financial_quarter_yfinance_list

- **上游 SQL**：[raw_yearly_financials_yfinance](#raw_yearly_financials_yfinance)
- **HTTP API endpoint**：無（純 JSONB 展開）
- 設計理念（規則 14, 15）：季度屬離散事件（每公司只有真正揭露的那幾季有資料）。事件母體由 `raw_yearly_financials_yfinance.financials.eps.ttm_quarters ∪ net_income.ttm_quarters` 告訴我們,而非規則性時間格點。與 `financial_quarter_list` (FinMind 版) 結構完全一致,只差資料源。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_yearly_financials_yfinance.stk_code` |
| `quater` | DATE | 該季最後一日（如 `2024-12-31`、`2024-09-30`） | `financials.eps.ttm_quarters[] ∪ financials.net_income.ttm_quarters[]` |

> 註：欄位名 `quater` 沿用 [financial_quarter_list](#financial_quarter_list) 現有拼字以維持下游 align。

---

## raw_quarterly_financials_yfinance

- **上游 SQL**：[financial_quarter_yfinance_list](#financial_quarter_yfinance_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials/yfinance?as_of={quarter}`
  - 上游：yfinance Python Library（Yahoo Finance）。與 FinMind 版同一輸出 spec,免 token、限流寬鬆。
- 設計理念（規則 1, 9）：把 HTTP 呼叫從 view 內攤平拆出來,避免下游 `financial_quarterly_yfinance` 在單一 SELECT 中觸發數百次同步 HTTP 造成 `statement_timeout`。這也是 [financial_quarterly](#financial_quarterly) 章節提到的「兩段式重構」在 yfinance 版的落實。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_quarter_yfinance_list.stk_code` |
| `financials` | JSONB | `/financials/yfinance` endpoint 整包 JSON（結構與 raw_yearly_financials_yfinance 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `quater`） | `financial_quarter_yfinance_list.quater` |

---

## financial_quarterly_yfinance

- **上游 SQL**：[raw_quarterly_financials_yfinance](#raw_quarterly_financials_yfinance)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [financial_quarterly](#financial_quarterly)、[financial_yearly_yfinance](#financial_yearly_yfinance) align,只差在資料源 (yfinance vs FinMind) 以及以季底為關鍵。
- 設計理念（規則 13）：不做 WHERE 過濾,保留 raw 母體的所有 rows。
- 型別安全：所有 numeric 欄位一律走 `->>` (回傳 text) 再 `::NUMERIC`,jsonb null 會安全轉為 SQL NULL,避免 `cannot cast jsonb null to type numeric`。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_quarterly_financials_yfinance.stk_code` |
| `as_of` | DATE | 本筆指標的查詢基準日 | `financials.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `financials.stock_id` |
| `eps_ttm` | NUMERIC | EPS TTM | `financials.eps.ttm` |
| `latest_quarter_date` | DATE | 最新可得的單季財報日 | `financials.eps.latest_quarter_date` |
| `latest_quarter_eps` | NUMERIC | 最新單季的 EPS | `financials.eps.latest_quarter_value` |
| `net_income_ttm` | NUMERIC | 稅後淨利 TTM | `financials.net_income.ttm` |
| `latest_quarter_net_income` | NUMERIC | 最新單季稅後淨利 | `financials.net_income.latest_quarter_value` |
| `operating_margin_pct` | NUMERIC | 營業利潤率 (%) | `financials.operating_margin_pct` |
| `revenue_ttm` | NUMERIC | 營收 TTM（取自財報） | `financials.revenue_ttm_from_financial_statements` |
