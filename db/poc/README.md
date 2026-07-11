# db/poc — PoC SQL 說明

> 整體 db / pipeline 設計理念請見 [`db/README.md`](../README.md)。本目錄是其中 `poc` schema（研發層）的實作。

## 規則

> 規則分五組：語法層、`_list.sql` 角色、資料邊界與事件母體、正規化與欄位處理、執行環境相容性（pop schema / pg_ivm / seed pattern）。規則編號保留歷史順序以便追溯 commit 討論，新規則排在尾端（rule 17, 18, 20）。

### 語法層

1. 非 `_list` 結尾的 SQL **禁止** 使用 mutable 操作（例如 `::DATE`, `http_get`）。
2. `_list` 結尾的 SQL **不可** 呼叫 `http_get_content`。
3. 若需要 mutable 邏輯或型別轉換,請封裝為 immutable function 放在 `db/settings.sql`。
4. 每個 PoC SQL 對應一個 table/view。

### `_list.sql` 角色

5. `_list` 表把資料模型的實體（chain、company、quarter…）落到一張慢慢增長的表,下游 view 透過
incremental materialized view 建構,可以與 `_list` 表同步擴充資料,避免一次性大量發查 API。
6. 每張表的 rows 都必須是唯一的。
7. `_list.sql` 的欄位即為下游 view 的 primary key。
8. **`_list.sql` = seed table**。所有會被 pop schema 逐步 `LIMIT` 增量填充的表都以 `_list.sql` 結尾命名；判斷是否有上游只要檢查檔內是否引用 `{{ schema }}`：
    - **沒引用 `{{ schema }}`** → 純上游 seed，從 endpoint / 日期產生器看起（例：`chain_list` 直接打 `/chains`、`financial_quarter_yfinance_list` 只靠 date generator）。【例外】含 `{{ schema }}` 但檔名不含 `_list` 的 seed，必要時需改名（已發生：`product_revenue_filer_scope` → `product_revenue_filer_scope_list`）。
    - **引用 `{{ schema }}`** → 上游來自其他 poc 表（例：`company_list` 上游是 `chain_info_list`、`dividend_event_list` 上游是 `raw_dividend_history`）；這種 `_list` 是「事件攤平母體」，仍屬 seed table 角色，仍必須以 `_list.sql` 命名。
    - `pipeline.py` 的 `seed_tables` 掃描所有 `_list.sql` 對應到 `pop.<name>` 空表，供 PoC 驗證期逐步增量填充使用（填充節奏 SOP 見頂層 [`README.md#LIMIT COUNT 選取`](../../README.md#memo)）。
9. `raw_` 開頭的SQL用途是抓取 app/main.py 的 endpoint API return 結果
11. `raw_` 開頭的SQL,需搭配一個 `_list.sql`作為上游,`_list.sql` 的邏輯要能指定 endpoint 的 API calling 可以遍歷所有 endpoint function 可能的 input ,但請避免重複的結果抓取。(e.g., 雖然「月營收」可以以日為單位去 call,但沒有必要在時間軸上這麼密集的抓取資料,最好只針對公佈日去抓取資料)

### 資料邊界與事件母體

13. 避免使用 WHERE 條件,讓 POC 階段會 full scan 整個上游表,應透過對 endpoint 的資料源的理解,從 相關 endpoint 找出資料邊界,融入到 `_list.sql` 表的邏輯中,讓下游的 `raw_` 表資料爬取可以有合理的起始點。
    - **例外**：`_list.sql` 允許 `WHERE <field> IS NOT NULL` 過濾「不可用事件」（例：某事件 key 為 NULL 的歷史列）,因為此類列對下游 `as_of` API 呼叫無意義。這屬資料品質層清理,不算隱藏 raw 母體邊界。但在動用本例外前,先見 **rule 17**— 優先換一個不會 NULL 的事件 key，而不是用 WHERE 掃掉 NULL 列。
14. 不同時間維度的資料,如 monthly, yearly, quarterly 資料,應基於不同的 `_list.sql` 來設定母體範圍
15. **事件性資料應建立事件層 endpoint 作為 `_list` 母體。** 當資料本質為離散事件（股利除息、產品營收自願申報）而非連續時間序列時：
    - **禁止**用規則性時間格點（每月月首、每年年初）作為 `_list` 母體,因為 as_of endpoint 只會回「該日期前最後一筆」,對格點採樣會產生大量重複命中的舊事件,浪費 API 也讓下游看到冗餘 rows。
    - 應在 `app/main.py` 新增「事件母體」endpoint（例：`/dividend/history` 回整段除息歷史 events array、`/product-revenue/filers?ym=&market=` 回該月該市場真正申報的公司清單）。
    - `_list.sql` 透過該事件母體 endpoint 產出「實際有事件的 (stk_code, event_date)」,下游 `raw_*` 只對真正有事件的 (id, date) 呼叫 as_of endpoint。
    - Rule 11 的「避免重複」本質是「不對沒事件的日期打 API」;規則性格點只是規則 15 的特例（月營收每月都公佈,格點恰等於事件母體）。

### 正規化與欄位處理

非 `raw_` 、非 `_list.sql` 的 view 定位為「將 raw JSON 攤平成強型列的正規化層」，下列規則針對這層。

10. 非 `raw_`、非 `_list.sql` 的 sql 目的是做 JSON 內容的欄位正規化，請以資料可用性來決定如何整理資料。
12. 請把民國年月日整合成西元年月日（以 DATE 來存）。

### 執行環境相容性（pop schema / pg_ivm / seed pattern）

poc schema 的 SQL 會經 `pipeline.py` 展開成 pop schema 的 [pg_ivm](https://github.com/sraoss/pg_ivm) incremental materialized view。因此 poc SQL 除了「邏輯正確」還要同時滿足「pg_ivm 相容」與「seed pattern 相容」。

16. **只用 INNER JOIN 與 CROSS JOIN**。pg_ivm 不支援 `LEFT / RIGHT / FULL OUTER JOIN`，連 `LEFT JOIN LATERAL` 也不行。攤平 JSON array 時若要保留空 array 的父列，請在上游 raw view 內以 `COALESCE(json_field, '[]'::jsonb)` 保證非空，再用 `INNER JOIN LATERAL jsonb_array_elements(...)` 或 `CROSS JOIN LATERAL jsonb_array_elements(...)` 攤平，不要用 `LEFT JOIN LATERAL`。
    - 同理避免：window functions、`DISTINCT`（除非上游本身無重複，可省略）、`GROUP BY`、WHERE 內的 subquery、CTE（WITH clause）。若邏輯必須用到，請看能否推到上游 `raw_` 表或包成 `db/settings.sql` 的 immutable function。
17. **事件 key 選擇 (取代 rule 13 例外的默認作法)**。當事件層 endpoint 回傳多個候選日期欄位（如 `/dividend/history` 回 `cash_ex_dividend_date` / `stock_ex_dividend_date` / `reference_date` / `announcement_date`），`_list.sql` 攤平事件的 key 必須選「所有已知歷史列都非 NULL」的欄位（如 `reference_date`），不要選「少數為 NULL」的欄位（如 `cash_ex_dividend_date` — 只有股票股利、無現金股利的年份為 NULL）。
    - 判斷方式：先 sample 歷史 event 統計每個候選欄位的 NULL 率；NULL 率 = 0% 的欄位優先。
    - 若所有候選都偶有 NULL，才動用 rule 13 例外的 `WHERE <key> IS NOT NULL`，並在 SQL 註解說明原因。
18. **SQL 演進策略：保護 pop schema 累積的資料，新增優先於修改**。pop schema 已累積的 raw / 正規化 view 背後都代表實際投於 API 抽取的時間與額度，既有資料實際上非常寶貴。
    - **不可在正常 refresh 時 `DROP SCHEMA pop CASCADE`**。任何 refresh 流程都必須用 `CREATE SCHEMA IF NOT EXISTS pop` 與 `CREATE TABLE IF NOT EXISTS pop.<seed>`。真的要從零重建時走顯式 `recreate=True` 開關（見 [`pipeline.py: create_mat_views()`](../../pipeline.py)）。
    - **新版 SQL 優先「新增另一張」，不要就地改寫既有 SQL**。若新推行的 SQL 會導致 view 欄位規格變動（新增 / 刪除 / 重命名欄位、換上游 endpoint、換 primary key），優先新增一張新名稱的 SQL（如 `<name>_v2.sql` / `<name>_yfinance.sql`）並讓新舊兩張並行一段日子，避免 pop 表因 schema drift 被 drop 重建、重新抽一次 API。只有在規格完全相容（同 primary key、只修 bug 不改欄位）或與使用方確認舊張可棄時，才就地改寫既有 SQL。
20. **不同限流 / 不同資料源的同型態 endpoint 要分流**。當同一資料層（如年頻財報、除息事件）同時有多個上游資料源實作（FinMind vs yfinance、公開資訊觀測站 vs 商業 API），各實作的限流（rate limit / daily quota）差異很大時，兩邊不可共用同一張 `_list.sql` / `pop.<seed>` 空表，否則快的那邊會被慢的那邊拖累。
    - **作法**：拆成兩張 `_list.sql`，內容可以完全相同（如 `financial_year_list` 與 `financial_year_yfinance_list`，或 `financial_month_list` 與 `financial_month_twse_list`），重點不在邏輯差異，而在兩張對應到獨立 `pop.<seed>` 空表，可各自以不同 doubling limit 進度填充。下游 `raw_*` view 對應換到新 seed。
    - **命名慣例**：保留原名給「默認或原始資料源」（路徑上常為 FinMind），新增另一張以資料源名稱作尾綴（`_yfinance_list`、`_twse_list`），對應的 raw view 也沿用同尾綴，以便 diff 最小並保留既有下游不動。
    - **例子1**：`financial_year_list` (FinMind、300 req/hr) vs `financial_year_yfinance_list` (yfinance、每小時數千次)。yfinance 可先拉高 LIMIT 快速拉齊，FinMind 保守進度，兩邊互不阻塞。
    - **例子2**：`financial_month_list` (FinMind `/api/company/{stock_id}/revenue`、300 req/hr) vs `financial_month_twse_list` (TWSE OpenAPI + MOPS t21sc03、TWSE OpenAPI 無明顯 quota + MOPS 內部有 24h cache，實際寬鬆很多)。月頻資料母體實際上非常大（~1900 家 × 每家實際上市以來月份），共用 seed 時 FinMind 建議保守值 1/tick 會拉低 TWSE 端進度；拆分後 TWSE 端可獨立以 4/tick（初版，後續 probe）壓縮拉齊時間。
    - Rule 20 與 rule 14、rule 15 相輔：rule 14 讓不同時間維度分 `_list`（monthly / yearly / quarterly）；rule 15 讓事件性 vs 連續型資料分 `_list`；rule 20 讓不同上游實作分 `_list`。
21. **展開型純 view 若下游多方消費，要物化成 `_list` 切斷 lateral 傳染**。當一張 view 用 `CROSS JOIN LATERAL jsonb_array_elements(...)` 把 `raw_*` 的 JSONB 攤成多列（一列變 N 列的 fan-out），且有**多張下游** view 直接讀它，pg_ivm 展開整條 SQL 時每個下游都會各自把 lateral 重新算一遍。若那條 lateral 鏈上游是 `raw_*`（背後掛 `http_get_content`），每次下游 refresh 都會**間接**觸發上游 endpoint 重複被呼叫（即使 `raw_*` 已經是 mat view，pg_ivm 對 view chain 的展開行為仍會把整條路徑作為單一表達式重新推算，導致效能崩塌）。
    - **判斷方式**：非 `raw_`、非 `_list` 的展開 view 若被 ≥ 2 張下游引用，或該 view 本身的 fan-out 倍率高（一列 → 幾十/幾百列），就應該把它改造成 `_list`。
    - **作法**：把該純 view rename 成 `<name>_list.sql`（內容不變），讓 `pipeline.py` 的 `seed_tables` 掃描時自動把它當 seed，物化成 `pop.<name>_list` 表。下游 view 從此讀 pop 表而非 lateral 展開的臨時結果。
    - **例子**：`chain_info.sql` → `chain_info_list.sql`。`raw_chain_info` 每列（47 條鏈）經三層 `CROSS JOIN LATERAL` 攤成 ~2200 列公司清單。下游 `company_list` 對它 `SELECT DISTINCT`，若走純 view chain，等於每次 refresh 都把 47 鏈的 JSON 展開一次；改成 `_list` 後 `company_list` 直接讀 `pop.chain_info_list` 表，lateral 只在 seed 物化時算一次。
    - **與 rule 8 的差別**：rule 8 的 `_list` 是「事件母體 seed」（chain_list / dividend_event_list…）；rule 21 的 `_list` 是「展開結果 seed」（原本是純正規化 view，只是因為多方消費 + 高 fan-out 而被升格）。命名慣例一致（都以 `_list.sql` 結尾）。
    - **與 rule 18 的關係**：rule 18 要求 SQL 演進時盡量「新增另一張」而非就地改寫；但 rename 是 schema-level 的 identity 變化，pop 表本來就要重建，屬於 rule 18 允許的「規格完全變」情境（此時舊 pop 表可安全 drop 因為它不對應任何 API 抽取成本，僅是 JSON 展開）。

## Seed 填滿時間 · 存放空間估算

> 本章估算在現行 `batch_size.json` 下，各 `_list.sql` 對應的 `pop.<seed>` 空表完全填滿母體所需的時間，以及填滿後 pop schema 整體存放空間。與 v0.0.10 五次補丁（月營收分流）直接相關。

### 估算前提

- **排程**：pg_cron 每 **1 分鐘** tick 一次 (`Pipeline.setup_schedules(period_minutes=1)`)，每 tick 每張 seed insert `batch_size.json` 的 `row_cnt` 列。
- **進度假設**：不考慮 `probe_seed_insert_limit` 後續 doubling（實際可能更快）、不考慮 API 限流或重試微延。
- **公司母體**：上市約 1,030 + 上櫃 約 870 = 約 **1,900** 家（來自 `company_list` DISTINCT `chain_info_list`）。
- **中位上市年數**：25 年（台股上市中位典型值；新公司拉低、老公司拉高）。
- **提醒**：這些數字是 **PoC 估算**，不取代實際 `pg_relation_size` 監控。

### 母體估算方法（多張 seed 共用）

各 seed 的母體列數估算來自純 SQL 推斷（`generate_series`、JSONB fan-out `jsonb_array_elements`、`CROSS JOIN LATERAL`）或上游 raw JSON 的直接展開。欄位意義見下方表格「母體估算方法」列。

### 存放空間估算方法（by 欄位型態）

以 PostgreSQL 表 heap tuple 估算，不含 TOAST 壓縮優化（JSONB > 2 KB 時 PostgreSQL 實際會 zlib 壓縮到 約 40-60%，估算屬保守上限）：

**Seed 本體** 約 60 B / 列：
- Heap tuple header + null bitmap + alignment：24-32 B
- `stk_code TEXT` 短字串（英數 4-6 char）：含 1 B length 共 約 8 B
- `DATE`：4 B
- BTree PK index（(stk_code, date)）：約 20 B / 列
- 小計：約 60 B / 列（tuple header + 索引，取保守上限）

**raw_* JSONB payload** by endpoint response 結構抽樣估均值：
- 月營收 (`/revenue`, `/revenue/twse`)：`latest_month_label` `latest_month_value` `latest_month_yoy_pct` `ttm_value` `ttm_yoy_pct` + wrapping → **約 1.2 KB**
- 年報 (`/financials`)：EPS 年度、revenue、net_income、margins、growth 共 15-20 數字欄位 → **約 4 KB**（yfinance 版略少 3.5 KB）
- 季報 (`/financials/yfinance?as_of=quarter`)：**約 2.5 KB**
- 除息單筆 (`/dividend`)：cash_dividend, stock_dividend, cash_ex_dividend_date, cash_capital_reduction 等 → **約 0.7-0.8 KB**
- 除息全歷史 (`/dividend/history`)：約 15 events 陣列 → **約 12 KB / 家**
- Product revenue (`/product-revenue`)：少於 10 行產品線 → **約 3 KB**
- Product revenue filers (`/product-revenue/filers`)：`co_ids` 陣列 ~200 家 × 6 char + wrap → **約 6 KB**
- Chain info (`/api/chain/{ic_code}`)：segments dict + 展開前公司清單 → **約 12 KB / 條**
- Company info (`/api/company/{id}`)：基本資料 + 主要營業項目 → **約 5 KB**

> pg_ivm 實作上仍會呼叫 PostgreSQL 的 TOAST 壓縮；本估算屬保守上限，實際磁碟需求可能為估算值的 40-60%。

### 各 seed 填滿時間與空間估算總表

填滿時間 = 母體列數 ÷ (batch_size × 1440 分鐘/天)。

| seed | 母體列數 | 母體估算方法 | batch_size (row/min) | 填滿時間 | raw payload / 列 | 填滿後 raw 存放 | seed 本體存放 | 總計 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `product_revenue_filer_scope_list` | 120 | 近 5 年 60 月 × 2 市場 (sii+otc) — 由 `product_revenue_filer_scope_list.sql` 的 `INTERVAL '5 years'` × `VALUES('sii'),('otc')` 卡氏積直接產生 | 4 | 0.5 小時 | ~6,000 B | 703.1 KB | 7.0 KB | 710.2 KB |
| `product_revenue_filer_list` | 24,000 | 60 月 × 2 市場 × 平均 ~200 家申報 = 24,000 — 每列來自 `raw_product_revenue_filers.filers.co_ids` 攝平 | 2 | 8.3 天 | ~3,000 B | 68.7 MB | 1.4 MB | 70.0 MB |
| `dividend_event_list` | 28,500 | 1,900 家 × 平均 15 event/家 (20 年 × 0.75 除息/年) — 由 `raw_dividend_history.events` 陣列 fan-out | 1 | **19.8 天** | ~800 B | 21.7 MB | 1.6 MB | 23.4 MB |
| `dividend_event_yfinance_list` | 28,500 | 同上（yfinance 版），母體相同但資料源不同 | 12 | 1.6 天 | ~700 B | 19.0 MB | 1.6 MB | 20.7 MB |
| `financial_year_list` | 47,500 | 1,900 家 × 25 年（中位上市年數）— 由 `generate_series(incorporation_date, CURRENT_DATE, '1 year')` 產生 | 22 | 1.5 天 | ~4,000 B | 181.2 MB | 2.7 MB | 183.9 MB |
| `financial_year_yfinance_list` | 47,500 | 與 financial_year_list 內容完全一致（rule 20 分流），僅 seed 表獨立 | 16 | 2.1 天 | ~3,500 B | 158.5 MB | 2.7 MB | 161.3 MB |
| `financial_quarter_yfinance_list` | 38,000 | 1,900 家 × 20 季（yfinance 提供近 5 年）— 由 `raw_quarterly_financials_yfinance` 已抓的 quarter list 反查 | 2 | **13.2 天** | ~2,500 B | 90.6 MB | 2.2 MB | 92.8 MB |
| `chain_list` | 47 | 47 條產業鏈 — 由 `/api/chains` 一次抓完（服務內硬編碼 IC_CHAINS 常數） | 2 | 0.4 小時 | — | — | 2.8 KB | 2.8 KB |
| `chain_info_list` | 2,200 | ~2,200 = 47 鏈 × 平均 47 家/鏈 — 由 `raw_chain_info.segments` 3 層 LATERAL 展開（rule 21） | 1024 | < 0.1 小時 | — | — | 128.9 KB | 128.9 KB |
| `company_list` | 1,900 | 1,900 = 上市 ~1,030 + 上櫃 ~870 — 由 `chain_info_list` DISTINCT 而來 | 1 | 1.3 天 | — | — | 111.3 KB | 111.3 KB |
| **`financial_month_list`** | **570,000** | 1,900 家 × 300 月（平均上市月數，中位 25 年）— 由 `generate_series(incorporation_date, CURRENT_DATE, '1 month')` | 1 | **395.8 天 (~13.2 月)** | ~1,200 B | 652.3 MB | 32.6 MB | **684.9 MB** |
| **`financial_month_twse_list`** | **570,000** | 與 financial_month_list 內容完全一致（rule 20 分流），僅 seed 表獨立 | 4 | **99.0 天 (~3.3 月)** | ~1,200 B | 652.3 MB | 32.6 MB | **684.9 MB** |

### 非-seed 型 raw view（直接從上層 seed 抽取，不單獨列入 batch_size）

| raw view | 列數 | 母體來源 | payload / 列 | 存放空間 |
| --- | ---: | --- | ---: | ---: |
| `raw_company_info` | 1,900 | company_list | ~5,000 B | 9.1 MB |
| `raw_chain_info` | 47 | chain_list | ~12,000 B | 550.8 KB |
| `raw_dividend_history` | 1,900 | company_list | ~12,000 B | 21.7 MB |
| `raw_dividend_history_yfinance` | 1,900 | company_list | ~12,000 B | 21.7 MB |

### 總計

- **全部 pop.raw_* JSONB** 約 **1.9 GB**（TOAST 壓縮後實際磁碟可能降至 40-60%）
- **全部 pop.<seed> 本體** 約 **77.7 MB**
- **合計估算** 約 **1.9-2.0 GB**

### 對作業方針的意義

1. **首要瓶頸 = `financial_month_list` (FinMind)**：現行 `batch_size=1` 下需約 396 天。FinMind 免費層 300 req/hr = 5 req/min，理論上 `batch_size` 可推到 4-5，但需留安全餘裕避免 burst 遭 limit。probe 後從 1 → 4 可壓至約 100 天。
2. **次要瓶頸 = `financial_month_twse_list`**：拆分後初版 4/tick = 99 天。TWSE OpenAPI 無明顯 quota + MOPS 有 24h cache，probe 後可拉高 8-16/tick 壓至 25-50 天。
3. **第二層 tuning 對象 = `dividend_event_list` (FinMind)** 與 **`financial_quarter_yfinance_list`**：兩邊都 10-20 天，可接受但仍可依需往上推。
4. **存放面**：1.9 GB 對現代 SSD 不是問題。若未來拓展到全歷史（`INTERVAL '20 years'` 代替 `INTERVAL '5 years'`），`product_revenue_filer_*` 系列列數需 ×4，就需優先 tune batch_size。

### 估算腳本從哪來？

本章所有數字都來自 `db/poc/scripts/estimate_seed_fill_and_storage.py`（同 commit 一併新增），可重新執行以在母體假設或 `batch_size.json` 變更後重新產生。

## 章節索引

1. [chain_list](#chain_list)
2. [raw_chain_info](#raw_chain_info)
3. [chain_info_list](#chain_info_list)
4. [company_list](#company_list)
5. [product_revenue_filer_scope_list](#product_revenue_filer_scope_list)
6. [raw_product_revenue_filers](#raw_product_revenue_filers)
7. [product_revenue_filer_list](#product_revenue_filer_list)
8. [raw_product_revenue](#raw_product_revenue)
9. [product_revenue](#product_revenue)
10. [raw_company_info](#raw_company_info)
11. [company_basic_info](#company_basic_info)
12. [company_business_items](#company_business_items)
13. [financial_year_list](#financial_year_list)
14. [raw_yearly_financials](#raw_yearly_financials)
15. [financial_year_yfinance_list](#financial_year_yfinance_list)
16. [raw_yearly_financials_yfinance](#raw_yearly_financials_yfinance)
17. [financial_yearly_yfinance](#financial_yearly_yfinance)
18. [financial_month_list](#financial_month_list)
19. [raw_monthly_revenue](#raw_monthly_revenue)
20. [monthly_revenue](#monthly_revenue)
21. [financial_month_twse_list](#financial_month_twse_list)
22. [raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
23. [monthly_revenue_twse](#monthly_revenue_twse)
24. [raw_dividend_history](#raw_dividend_history)
25. [raw_dividend_history_yfinance](#raw_dividend_history_yfinance)
26. [dividend_event_list](#dividend_event_list)
27. [dividend_event_list_yfinance](#dividend_event_list_yfinance)
28. [raw_dividend](#raw_dividend)
29. [dividend](#dividend)
30. [raw_dividend_yfinance](#raw_dividend_yfinance)
31. [dividend_yfinance](#dividend_yfinance)
32. [financial_quarter_list](#financial_quarter_list)
33. [financial_quarterly](#financial_quarterly)
34. [financial_quarter_yfinance_list](#financial_quarter_yfinance_list)
35. [raw_quarterly_financials_yfinance](#raw_quarterly_financials_yfinance)
36. [financial_quarterly_yfinance](#financial_quarterly_yfinance)
37. [ohlcv_daily_twse_list](#ohlcv_daily_twse_list)
38. [raw_ohlcv_daily_twse](#raw_ohlcv_daily_twse)
39. [ohlcv_daily_twse](#ohlcv_daily_twse)
40. [ohlcv_daily_tpex_list](#ohlcv_daily_tpex_list)
41. [raw_ohlcv_daily_tpex](#raw_ohlcv_daily_tpex)
42. [ohlcv_daily_tpex](#ohlcv_daily_tpex)

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

## chain_info_list

- **上游 SQL**：[raw_chain_info](#raw_chain_info)
- **HTTP API endpoint**：無（純 JSONB 展開）
- **角色**：seed（`_list`）。詳見 **rule 21**——因為此 view 對 `raw_chain_info.segments` 做 3 層 `CROSS JOIN LATERAL`（一列 47 鏈 → ~2200 列公司），且下游 `company_list` 是唯二消費者之一。若維持純 view chain，pg_ivm 展開時會把 lateral 重複計算並間接讓 `raw_chain_info` 的 `chain_list` 上游被反覆重掃，等於間接呼叫 47 次 `/api/chain/{ic_code}`。改造成 `_list` 後由 pipeline 物化到 `pop.chain_info_list`，下游只讀該物化表。

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

- **上游 SQL**：[chain_info_list](#chain_info_list)
- **HTTP API endpoint**：無（純 SQL `DISTINCT`）

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號（例 `2330`） | `chain_info_list.stk_code` |
| `company_name` | TEXT | 公司名稱（產業鏈樹中的顯示名稱） | `chain_info_list.company_name` |

---

## product_revenue_filer_scope_list

- **上游 SQL**：無外部（純 SQL `generate_series` + `VALUES` 產生時間邊界 × 市場）
- **HTTP API endpoint**：無（是 `_list` seed table,不呼叫 HTTP,rule 2）
- **設計理念（rule 15 支援）**：PoC 階段預設「近 5 年」的 MOPS 產品營收全公司掃描範圍。若未來要擴大到完整歷史,只需修改本檔的 `INTERVAL '5 years'`,此為**唯一時間邊界調整點**,下游 `raw_product_revenue_filers` / `product_revenue_filer_list` / `raw_product_revenue` / `product_revenue` 均自動跟隨擴大。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `ym` | TEXT | 民國年月 5 碼字串（例 `'11312'` = 民國 113 年 12 月） | `generate_series(now - 5y, now, '1 month')` 民國化 |
| `market` | TEXT | 市場別（`'sii'` = 上市 / `'otc'` = 上櫃） | `VALUES ('sii'), ('otc')` |

---

## raw_product_revenue_filers

- **上游 SQL**：[product_revenue_filer_scope_list](#product_revenue_filer_scope_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/product-revenue/filers?ym={ym}&market={market}`
  - 上游資料源：[公開資訊觀測站 (MOPS) ajax_t05st08_all](https://mops.twse.com.tw/mops/web/ajax_t05st08_all)（該月該市場所有申報「各項產品業務營收」的公司清單）
- 設計理念（rule 15）：MOPS 各項產品業務營收 IFRS 後改自願申報,非每公司每月都有申報。本 raw 對每 `(ym, market)` 打一次 filers endpoint,取得「真正有申報的 co_id 陣列」,供下游 `product_revenue_filer_list` 攤平為 (co_id × ym) 事件母體。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `ym` | TEXT | 民國年月 5 碼字串 | `product_revenue_filer_scope_list.ym` |
| `market` | TEXT | 市場別 | `product_revenue_filer_scope_list.market` |
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

## financial_year_yfinance_list

- **上游 SQL**：[company_basic_info](#company_basic_info)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **設計理念（rule 20）**：內容完全等同 `financial_year_list`,拆成兩張目的在於「分流爬取」：`raw_yearly_financials`（FinMind）與 `raw_yearly_financials_yfinance` 對應到不同 `pop.<seed>` 空表，可各自以不同的 doubling limit 進度填充。yfinance 限流寬（每小時可數千次），可以先拉高 LIMIT 快速拉齊；FinMind 慢，保持保守進度，兩邊互不阻塞。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `year_start_date` | DATE | 公司歷年的 1 月 1 日（從設立年到今年） | `generate_series(trunc_year(incorporation_date), CURRENT_DATE, '1 year')` |

---

## raw_yearly_financials_yfinance

- **上游 SQL**：[financial_year_yfinance_list](#financial_year_yfinance_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/financials/yfinance?as_of={year_start_date}`
  - 上游：yfinance Python Library（Yahoo Finance 非官方 wrapper）。與 FinMind 版同一輸出 spec,限流寬鬆許多、免 token;適合產 PoC 階段對比 / 其他 ad-hoc 研究。
- **分流點（rule 20）**：上游 seed 已從 `financial_year_list` 換為 `financial_year_yfinance_list`，與 `raw_yearly_financials` 從同一張 `financial_year_list` 向下分岐的舊架構不同；兩張 raw view 各自對應獨立 `pop.<seed>` 空表，各自進度填充。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_year_yfinance_list.stk_code` |
| `financials` | JSONB | `/financials/yfinance` endpoint 整包 JSON（結構與 raw_yearly_financials 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日 | `financial_year_yfinance_list.year_start_date` |

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
- **服務的下游**：[raw_monthly_revenue](#raw_monthly_revenue)（FinMind 版）
- 設計理念（規則 11, 14, 15）：月營收公布頻率為每月一次（次月 10 日前）,幾乎所有公司每月都有,規則性格點恰等於事件母體 — 為 rule 15 的退化特例。因此仍以「每月一次」的 as_of 遍歷所有月份切片,不必以日為單位密集抓取。
- **設計理念（規則 20）**：本 seed 專供 FinMind 版 raw_monthly_revenue。TWSE/MOPS 版已於 v0.0.10 五次補丁拆到獨立 seed [financial_month_twse_list](#financial_month_twse_list)。並行拆分目的在避免共用 seed 時慢端（FinMind 免費層 300 req/hr）拖累快端（TWSE OpenAPI + MOPS t21sc03,後者有 24h server-side cache,quota 寬鬆很多）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `month_start_date` | DATE | 從公司成立日所屬月份開始、每月 1 日 generate 一列,直到 CURRENT_DATE | `generate_series(...)` |

---

## raw_monthly_revenue

- **上游 SQL**：[financial_month_list](#financial_month_list)（FinMind 專屬 seed）
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue?as_of={month_start_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockMonthRevenue`
- 設計理念（規則 14）：月頻資料須有專屬 `_list`（financial_month_list）,不與年頻 dividend / financials 混用同一個 `financial_year_list`。
- 設計理念（規則 20）：TWSE/MOPS 版 [raw_monthly_revenue_twse](#raw_monthly_revenue_twse) 已拆到獨立 seed [financial_month_twse_list](#financial_month_twse_list)，本 raw 以 FinMind quota (300 req/hr = 5/min) 當上限，不被 TWSE/MOPS 的大跨步 batch 推進相互干擾。

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

## financial_month_twse_list

- **上游 SQL**：[company_basic_info](#company_basic_info)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **服務的下游**：[raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
- **設計理念（規則 20）**：內容完全等同 [financial_month_list](#financial_month_list)，拆成兩張目的在於「分流爬取」：`raw_monthly_revenue`（FinMind, 免費層 300 req/hr）與 `raw_monthly_revenue_twse`（TWSE OpenAPI + MOPS t21sc03; TWSE OpenAPI 無明顯 quota、MOPS 內部有 24h server-side cache）對應到不同 `pop.<seed>` 空表，可各自以不同的 batch limit 進度填充。TWSE 端限流寬，可拉高 `batch_size.json` 的 `financial_month_twse_list` 快速拉齊（初版 4/tick，後續由 `probe_seed_insert_limit` 推升），FinMind 端保持 1/tick 不被拖累。
- 內容與 [financial_month_list](#financial_month_list) 完全一致 — 拆分價值僅在於獨立 seed，不在邏輯差異。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `month_start_date` | DATE | 從公司成立日所屬月份開始、每月 1 日 generate 一列,直到 CURRENT_DATE | `generate_series(...)` |

---

## raw_monthly_revenue_twse

- **上游 SQL**：[financial_month_twse_list](#financial_month_twse_list)（TWSE 專屬 seed）
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue/twse?as_of={month_start_date}`
  - 上游：證交所體系雙來源。「最新一個月」：[TWSE OpenAPI](https://openapi.twse.com.tw/v1/opendata/t187ap05_L) / [TPEx OpenAPI](https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv) t187ap05;「歷史月營收」：[公開資訊觀測站 MOPS](https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_113_5_0.html) t21sc03 採用 IFRSs 後每月營業收入彙總表。
  - 與 FinMind 版同一輸出 spec,提供「官方」源頭供審計溯源,免 token、免限流。
- 設計理念（規則 20）：與 [raw_monthly_revenue](#raw_monthly_revenue) 分流爬取，各自對應獨立 `pop.<seed>` 空表。本 raw 可以拉高 batch_size 快速拉齊，不受 FinMind 300 req/hr 拖累。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_month_twse_list.stk_code` |
| `revenue` | JSONB | `/revenue/twse` endpoint 整包 JSON（結構與 raw_monthly_revenue 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `month_start_date`） | `financial_month_twse_list.month_start_date` |

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

---

## ohlcv_daily_twse_list

- **上游 SQL**：[company_basic_info](#company_basic_info)（過濾 `market = '上市'`）
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **角色**：seed（`_list`）— 每 `(stk_code, month_start_date)` 一列，作為 `/api/ohlcv` 事件母體。
- **設計理念（rule 15 · 母體大小）**：從 `company_basic_info.listing_date` 起，月粒度 `generate_series` 到 `CURRENT_DATE`。以 ~1300 檔上市 × 平均 15 年 × 12 月 ≈ 234k 列 seed 上限。
- **設計理念（rule 20 · 資料源分流）**：與 `ohlcv_daily_tpex_list` 分兩張獨立 seed。雖然 backend `/api/ohlcv` endpoint 對上市 / 上櫃同 URL，但兩市場股數量與交易日分佈不同，分成兩張獨立 seed 可各自控 `batch_size` 增量填滿；backend 內部仍依 `company_basic_info.market` 分發到不同 TWSE / TPEx 上游、保留 rule 20 資料源分流的層次性。
- **設計理念（rule 6 · rows 唯一）**：`(stk_code, month_start_date)` 天然唯一 — `generate_series` step `INTERVAL '1 month'` 且起點為月初。
- **設計理念（rule 13）**：`listing_date IS NOT NULL` 為技術性 guard（`generate_series` 起點不能是 NULL），非業務過濾。
- **每日新月觸發機制**：每月 1 日 `CURRENT_DATE` 推進到新月 → `generate_series` 上界改變 → 每檔股票多一列新的 `month_start_date` → pipeline `EXCEPT` anti-join 判定為新列並 INSERT → 觸發 raw 拉該股該月最新日 K。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `month_start_date` | DATE | 該月第一日（西元） | `generate_series(month_of(listing_date), CURRENT_DATE, INTERVAL '1 month')` |

---

## raw_ohlcv_daily_twse

- **上游 SQL**：[ohlcv_daily_twse_list](#ohlcv_daily_twse_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/ohlcv?stk_code={stk_code}&from={month_start}&to={month_end}`
  - 上游 endpoint：twstock_api 統一 `/api/ohlcv`（於 `app/main.py` 定義）— backend 依 `company_basic_info.market` 判別後分發到 [TWSE 證交所 · 個股日成交資訊 STOCK_DAY](https://www.twse.com.tw/zh/trading/historical/stock-day.html)（per-stock-per-month）。
  - Backend 策略：`range_days > 7` 走 `per_stock_month`，一次呼叫 TWSE STOCK_DAY 拿整月日 K（~20 個交易日）；相對 `per-stock-per-day` 節省 20× 上游流量。
  - 為何走 `/api/ohlcv` 而非直接打 TWSE：backend 已把 TPEx「張 / 仟元」單位對齊「股 / 元」、把民國年轉西元、把「漲跌方向 + 漲跌價差」合成 signed `change`；raw 層與 normalized 層 SQL 大幅簡化，且 TWSE / TPEx 兩條 pipeline 共享同一 backend endpoint（未來若上游 URL 變動只需改 backend）。
- **設計理念（rule 9 · one payload per event）**：一個 `(stk_code, month_start_date)` = 一列 payload；範圍為「該月 1 號 → 當月月底」（`month_start + INTERVAL '1 month' - INTERVAL '1 day'`）。
- **payload 空回情境**：`found=false`（stk_code 不在 `company_basic_info`）或該月無交易日 → `rows=[]`，正規化層 `CROSS JOIN LATERAL` 自然攤平 0 列。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `ohlcv_daily_twse_list.stk_code` |
| `month_start_date` | DATE | 該月第一日 | `ohlcv_daily_twse_list.month_start_date` |
| `ohlcv` | JSONB | 該股該月日 K JSON | `custom.http_get_content('/api/ohlcv?stk_code=X&from=Y&to=Z')` |

---

## ohlcv_daily_twse

- **上游 SQL**：[raw_ohlcv_daily_twse](#raw_ohlcv_daily_twse)
- **HTTP API endpoint**：無（純 JSONB 攤平）
- **設計理念（rule 16 · pg_ivm 相容）**：只用 `CROSS JOIN LATERAL jsonb_array_elements(ohlcv->'rows')` 攤平陣列成逐日一列。
- **設計理念（rule 12 · 民國年 → 西元）**：`/api/ohlcv` 已由 backend 統一轉為西元 ISO `'YYYY-MM-DD'`；normalized 層直接 `(item->>'trade_date')::DATE`，不再需要 `make_date + SUBSTRING` 展開民國年。
- **設計理念（rule 13）**：不做過濾；`found=false` 或 `rows=[]` 天然攤平 0 列。
- **NULL-safe**：`COALESCE(ohlcv->'rows', '[]'::jsonb)` 防 payload 缺 `rows` key。
- **欄位對齊 ohlcv_daily_tpex**：`market='sii'`；下游可直接 `UNION` 兩市場。
- **數字欄位處理**：backend 已把千分位逗號剝除、把 signed `+`/`-` 前綴合入 `change`、把單位對齊「股 / 元」，SQL 只需 `::NUMERIC` CAST。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `trade_date` | DATE | 交易日期（西元） | `rows[*].trade_date` |
| `stk_code` | TEXT | 股票代號 | `rows[*].stk_code` |
| `market` | TEXT | 市場別（固定 `'sii'`） | 常數 |
| `open` | NUMERIC | 開盤價 | `rows[*].open` |
| `high` | NUMERIC | 最高價 | `rows[*].high` |
| `low` | NUMERIC | 最低價 | `rows[*].low` |
| `close` | NUMERIC | 收盤價 | `rows[*].close` |
| `volume` | NUMERIC | 成交股數 | `rows[*].volume` |
| `trade_value` | NUMERIC | 成交金額（元） | `rows[*].trade_value` |
| `transaction_count` | NUMERIC | 成交筆數 | `rows[*].transaction_count` |
| `change` | NUMERIC | 漲跌（signed） | `rows[*].change` |

---

## ohlcv_daily_tpex_list

- **上游 SQL**：[company_basic_info](#company_basic_info)（過濾 `market = '上櫃'`）
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **角色**：seed（`_list`）— 每 `(stk_code, month_start_date)` 一列。
- **設計理念（rule 15 · 母體大小）**：~800 檔上櫃 × 平均 10 年 × 12 月 ≈ 96k 列 seed 上限。
- **設計理念（rule 20）**：與 `ohlcv_daily_twse_list` 分流，各自獨立 batch_size；backend 內部仍依 market 分發到不同上游（TWSE / TPEx）。
- **設計理念（rule 6 / rule 13）**：同 TWSE 版本設計理念。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info.stk_code` |
| `month_start_date` | DATE | 該月第一日（西元） | `generate_series(month_of(listing_date), CURRENT_DATE, INTERVAL '1 month')` |

---

## raw_ohlcv_daily_tpex

- **上游 SQL**：[ohlcv_daily_tpex_list](#ohlcv_daily_tpex_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/ohlcv?stk_code={stk_code}&from={month_start}&to={month_end}`
  - 上游 endpoint：twstock_api 統一 `/api/ohlcv`（於 `app/main.py` 定義）— backend 依 `company_basic_info.market` 判別後分發到 [TPEx 櫃買中心 · 個股日成交資訊 tradingStock](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/mi-pricing.html)（per-stock-per-month）。
  - URL 與 `raw_ohlcv_daily_twse` **完全相同格式**；TWSE / TPEx 分流僅存在於 seed 層（過濾 `market`），raw 層 URL 一致，backend 內部路由。
  - 統一 endpoint 好處：TPEx 上游「張 / 仟元」單位對齊已由 backend 自動 `× 1000` 處理，raw / normalized 層不再需要 `× 1000` 魔數（與 v2 相比）。
- **設計理念（rule 9）**：一個 `(stk_code, month_start_date)` = 一列 payload；範圍「該月 1 號 → 當月月底」。
- **空回情境**：個股該月未上櫃 / 停牌 → `rows=[]`，正規化層攤平 0 列。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `ohlcv_daily_tpex_list.stk_code` |
| `month_start_date` | DATE | 該月第一日 | `ohlcv_daily_tpex_list.month_start_date` |
| `ohlcv` | JSONB | 該股該月日 K JSON | `custom.http_get_content('/api/ohlcv?stk_code=X&from=Y&to=Z')` |

---

## ohlcv_daily_tpex

- **上游 SQL**：[raw_ohlcv_daily_tpex](#raw_ohlcv_daily_tpex)
- **HTTP API endpoint**：無（純 JSONB 攤平）
- **設計理念（rule 16）**：`CROSS JOIN LATERAL jsonb_array_elements(ohlcv->'rows')` 攤平陣列（與 `ohlcv_daily_twse` 完全一致）。
- **設計理念（rule 12）**：backend 已把民國年轉西元；normalized 層直接 CAST。
- **設計理念（rule 13）**：不做過濾。
- **NULL-safe**：`COALESCE(ohlcv->'rows', '[]'::jsonb)`。
- **欄位對齊 ohlcv_daily_twse**：`market='otc'`；欄位命名、單位皆對齊 TWSE 版。
- **單位對齊（v3 改動）**：backend 已把 TPEx 上游「張 / 仟元」統一 `× 1000` 對齊「股 / 元」，normalized 層 SQL **不再需要** 乘 1000 魔數（原 v2 的 `* 1000` 已消失）。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `trade_date` | DATE | 交易日期（西元） | `rows[*].trade_date` |
| `stk_code` | TEXT | 股票代號 | `rows[*].stk_code` |
| `market` | TEXT | 市場別（固定 `'otc'`） | 常數 |
| `open` | NUMERIC | 開盤價 | `rows[*].open` |
| `high` | NUMERIC | 最高價 | `rows[*].high` |
| `low` | NUMERIC | 最低價 | `rows[*].low` |
| `close` | NUMERIC | 收盤價 | `rows[*].close` |
| `volume` | NUMERIC | 成交股數（backend 已 × 1000 對齊） | `rows[*].volume` |
| `trade_value` | NUMERIC | 成交金額（元，backend 已 × 1000 對齊） | `rows[*].trade_value` |
| `transaction_count` | NUMERIC | 成交筆數 | `rows[*].transaction_count` |
| `change` | NUMERIC | 漲跌（signed） | `rows[*].change` |
