# db/poc — PoC SQL 說明

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
5. [raw_company_info](#raw_company_info)
6. [company_basic_info](#company_basic_info)
7. [company_business_items](#company_business_items)
8. [financial_year_list](#financial_year_list)
9. [raw_yearly_financials](#raw_yearly_financials)
10. [financial_quarter_list](#financial_quarter_list)
11. [financial_quarterly](#financial_quarterly)

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
