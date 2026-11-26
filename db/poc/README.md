# db/poc — PoC SQL 說明

> 整體 db / pipeline 設計理念請見 [`db/README.md`](../README.md)。本目錄是其中 `poc` schema（研發層）的實作。

## 規則

> 規則分五組：語法層、`_list.sql` 角色、資料邊界與事件母體、正規化與欄位處理、執行環境相容性（pop schema / pg_ivm / seed pattern）。規則編號保留歷史順序以便追溯 commit 討論，新規則排在尾端（rule 17, 18, 20）。

### 語法層

1. 非 `_list` 結尾的 SQL **禁止** 使用 mutable 操作（例如 `::DATE`, `http_get`）。
2. `_list` 結尾的 SQL **不可** 呼叫 `http_get_content`。
    - **例外：DAG 根節點 seed**。不引用 `{{ schema }}`、沒有任何上游的 `_list`（rule 8 的「純上游 seed」，目前只有 `chain_list` 打 `/chains`）可以呼叫一次 endpoint 取得實體清單——它沒有其他資料來源，改成 `raw_` 也還是需要一張上游 `_list`。條件：只打一個不帶參數的 URL（不隨列數放大），且排程頻率低（`chain_list` 為每日一次；URL 另有 `job_control.http_cache` 命中）。有上游的 `_list` 一律不可呼叫，應改讀已物化的上游表（例：`company_list` 讀 `chain_info_list`，不自己打 `/api/chain/*`）。
3. 若需要 mutable 邏輯或型別轉換,請封裝為 immutable function 放在 `db/setting.sql`。
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
    - **時間格點的邊界用產生器本身表達，不要「多產生再 WHERE 篩」**。例：季末日用 `generate_series(<對齊到季末月的起點>, CURRENT_DATE, INTERVAL '3 months')`，不要逐月展開再 `WHERE EXTRACT(MONTH ...) IN (3,6,9,12)`。`hidden.<seed>` 在每次 cron tick 都會整張重算再 EXCEPT，多產生的列就是每 tick 的額外成本（`mops_quarterly_financials_quarter_list` 實測 16.2ms → 3.8ms，輸出相同）。可用 MCP `crawl_backlog` 量每 tick 的選列成本。
    - **邊界要對齊資料源實際覆蓋，並用資料驗證**。`_list` 起點早於資料源實際可得日期時，每一列都是必定作廢的 API 呼叫（下游 raw 為 NULL / found=false 或內容全空）。上線後用 MCP `pop_raw_quality` 檢查各 raw 的可用率。
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

### 執行環境相容性（pop schema / pg_ivm / seed pattern / LineageX 靜態解析）

poc schema 的 SQL 會經 `pipeline.py` 展開成 pop schema 的 [pg_ivm](https://github.com/sraoss/pg_ivm) incremental materialized view。因此 poc SQL 除了「邏輯正確」還要同時滿足「pg_ivm 相容」、「seed pattern 相容」，以及「LineageX 靜態解析相容」（rule 22）。

16. **只用 INNER JOIN 與 CROSS JOIN**。pg_ivm 不支援 `LEFT / RIGHT / FULL OUTER JOIN`，連 `LEFT JOIN LATERAL` 也不行。攤平 JSON array 時若要保留空 array 的父列，請在上游 raw view 內以 `COALESCE(json_field, '[]'::jsonb)` 保證非空，再用 `INNER JOIN LATERAL jsonb_array_elements(...)` 或 `CROSS JOIN LATERAL jsonb_array_elements(...)` 攤平，不要用 `LEFT JOIN LATERAL`。
    - 同理避免：window functions、`DISTINCT`（除非上游本身無重複，可省略）、`GROUP BY`、WHERE 內的 subquery、CTE（WITH clause）。若邏輯必須用到，請看能否推到上游 `raw_` 表或包成 `db/setting.sql` 的 immutable function。
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
    - **例子3**：`company_list`（TWSE OpenAPI `raw_company_info`；yfinance `raw_dividend_history_yfinance` 原本也在此，I015 起兩條股利路徑都改讀 `company_basic_info_list`）vs `company_finmind_list`（FinMind `raw_dividend_history`）。共用時實測：`company_list` 以 36 家/分推進，FinMind 約 100 次後回 402 "Requests reach the upper limit"，之後每家公司的 `raw_dividend_history` 都被固定成 found=false 空列（seed 已插入、IMMV 不會再重抓），一輪下來 154/454 列作廢。拆分後 `company_finmind_list` 只讀 `pop.company_basic_info_list`（上市櫃，I015），以 ≤ 300/hr 獨立排程。
    - **判斷方式**：一張 `_list` 的直接下游 `raw_*` 若打到兩個以上不同限流的資料源，就該拆；可用 MCP `pop_raw_quality` 看各 raw 的 found=false 比例、`http_stats` 看各 endpoint 請求量確認。
    - Rule 20 與 rule 14、rule 15 相輔：rule 14 讓不同時間維度分 `_list`（monthly / yearly / quarterly）；rule 15 讓事件性 vs 連續型資料分 `_list`；rule 20 讓不同上游實作分 `_list`。
21. **展開型純 view 若下游多方消費，要物化成 `_list` 切斷 lateral 傳染**。當一張 view 用 `CROSS JOIN LATERAL jsonb_array_elements(...)` 把 `raw_*` 的 JSONB 攤成多列（一列變 N 列的 fan-out），且有**多張下游** view 直接讀它，pg_ivm 展開整條 SQL 時每個下游都會各自把 lateral 重新算一遍。若那條 lateral 鏈上游是 `raw_*`（背後掛 `http_get_content`），每次下游 refresh 都會**間接**觸發上游 endpoint 重複被呼叫（即使 `raw_*` 已經是 mat view，pg_ivm 對 view chain 的展開行為仍會把整條路徑作為單一表達式重新推算，導致效能崩塌）。
    - **判斷方式**：非 `raw_`、非 `_list` 的展開 view 若被 ≥ 2 張下游引用，或該 view 本身的 fan-out 倍率高（一列 → 幾十/幾百列），就應該把它改造成 `_list`。
    - **作法**：把該純 view rename 成 `<name>_list.sql`（內容不變），讓 `pipeline.py` 的 `seed_tables` 掃描時自動把它當 seed，物化成 `pop.<name>_list` 表。下游 view 從此讀 pop 表而非 lateral 展開的臨時結果。
    - **例子**：`chain_info.sql` → `chain_info_list.sql`。`raw_chain_info` 每列（47 條鏈）經三層 `CROSS JOIN LATERAL` 攤成 ~2200 列公司清單。下游 `company_list` 對它 `SELECT DISTINCT`，若走純 view chain，等於每次 refresh 都把 47 鏈的 JSON 展開一次；改成 `_list` 後 `company_list` 直接讀 `pop.chain_info_list` 表，lateral 只在 seed 物化時算一次。
    - **與 rule 8 的差別**：rule 8 的 `_list` 是「事件母體 seed」（chain_list / dividend_event_list…）；rule 21 的 `_list` 是「展開結果 seed」（原本是純正規化 view，只是因為多方消費 + 高 fan-out 而被升格）。命名慣例一致（都以 `_list.sql` 結尾）。
    - **與 rule 18 的關係**：rule 18 要求 SQL 演進時盡量「新增另一張」而非就地改寫；但 rename 是 schema-level 的 identity 變化，pop 表本來就要重建，屬於 rule 18 允許的「規格完全變」情境（此時舊 pop 表可安全 drop 因為它不對應任何 API 抽取成本，僅是 JSON 展開）。
22. **JSON 路徑運算子 `->`/`->>` 若包在函式呼叫裡，巢狀路徑的第一步要加括號，確保 LineageX 血緣分析工具（`tools/render_lineagex.py`）能正確追蹤上游欄位。**
    - **成因**：LineageX 底層用 `sqlglot` 把 SQL 解析成 AST，再抓出每個輸出欄位表達式裡的所有 `Column` 節點當作上游欄位。當一段巢狀 JSON 路徑（先 `->` 再 `->>`，例：`col->'row'->>'key'`）的**最外層（最左邊）運算子是單箭頭 `->`**，且整段表達式被包在**任何函式呼叫**的參數裡（不論是 `custom.parse_iso_date(...)` 這類自訂函式，還是任何 built-in 函式），`sqlglot` 的語法解析器會把 `col -> 'row'` 誤判成其他 SQL 方言的「lambda 參數箭頭」語法（例如 `list_transform` 之類高階函式的 `x -> expr`），導致 `col` 完全不會被解析成 `Column` 節點。LineageX 因此回報這個輸出欄位「沒有任何上游欄位」——即使該 view 透過 `pg_tool.get_dependent_views` 反查明顯有上游 view，也會被     `tests/sql/test_sql_column_lineage_coverage.py` 標記出來。
    - **觸發條件**：只有「巢狀路徑最外層是單箭頭 `->`」+「整段包在函式呼叫參數裡」同時成立才會誤判。只用 `->>` 銜接（不論巢狀幾層）不會有此問題；不包在函式呼叫裡（例如直接 `col->'row'->>'key' AS x`，或包在 `(...)::NUMERIC` 型別轉換裡）也不會有此問題——這兩種都能被 `sqlglot` 正確解析。
    - **作法**：把巢狀路徑的第一步明確加括號，例如 `custom.parse_iso_date((col->'row')->>'key')`。PostgreSQL 的 `->`/`->>` 本身是左結合，加括號不會改變執行結果，純粹是幫助 `sqlglot` 的解析器正確辨識成 JSON 取值運算而非 lambda 語法。
    - **驗證方式**：改完後執行 `python tools/render_lineagex.py`    （或執行 `pytest tests/sql/test_sql_column_lineage_coverage.py`），確認該欄位在 `output.json` 有非空的 direct source。
    - **已知案例**：`institutional_net_buy_sell.sql` 的 `trade_date`、`foreign_ownership.sql` 的 `last_change_date`，兩者原寫法都是 `custom.parse_iso_date(col->'row'->>'key')`，改成 `custom.parse_iso_date((col->'row')->>'key')` 後修正。

## Seed 填滿時間 · 存放空間估算

> 本章數字由 [`scripts/estimate_seed_fill_and_storage.py`](scripts/estimate_seed_fill_and_storage.py) 從 live DB 實測產生（取代舊版「1,900 家 × 300 月」之類的假設值）。母體邊界見各 `_list.sql`（rule 13 對齊資料源實際覆蓋後的版本）。

### 估算方法

- **母體列數**：直接 count `hidden.<seed>`（= 目前 SQL 定義下的母體）。依賴 `company_basic_info_list` 的 seed 在 hidden 中只看得到已爬到的公司，因此乘上「估計公司數 ÷ 已爬公司數」換算全量；估計公司數 = `chain_info_list` 的 DISTINCT `stk_code`（全量 2,250 家）× 基本資料可得率（約 83%，其餘公司 TWSE/TPEx OpenAPI 查無資料）。事件型 seed（`dividend_event_*`、`financial_quarter_yfinance_list`）以「已爬上游公司數」同理換算。
- **已爬列數**：`hidden ∩ pop`，只計仍落在新母體內的列（改邊界前爬過、已不在母體內的舊列不算進度）。
- **速率**：名目值讀 `cron.job` 的排程與 `LIMIT`；「估算採用」為觀察窗口內實際新增列數換算的 rows/hr（本表窗口 = IMMV 重複呼叫修正後的 15 分鐘，10:43–10:58 UTC）（取與名目的較小值），分母只計窗口內**實際有 cron 執行的分鐘**——主機睡眠時 Docker VM 暫停、cron 不會跑，睡眠空檔不計入。
- **存放**：seed 已爬 ≥ 50 列時，以「該 seed 下游所有 pop 表（raw_* + 正規化 IMMV，直到下一個 seed）的 `pg_total_relation_size` ÷ seed 已爬列數」為每列實測成本（含 TOAST、索引）；不足 50 列標「推估」，以相近 endpoint 的實測值代入。
- **含上游填滿**：下游 seed 最快也要等上游填滿，取自身與所有上游 seed 的最大值。
- 填滿時間是「主機持續運作」下的時間；主機睡眠期間爬取暫停，實際日曆時間會更長。

### 各 seed 填滿時間與空間估算總表

> 量測時間：2026-11-23 18:58；全量公司 2,250 家，基本資料可得率 82.6%（估計 1,859 家）。

| seed | 上游資料源 | 母體（全量估計） | 已爬 | 排程 | rows/hr（名目 → 估算採用） | tick 失敗率 | 自身填滿 | 含上游填滿 | 每列存放 | 填滿後存放 |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `financial_year_list` | FinMind | 56,442 | 86 | 1 列 / 60s | 60 → 56（實測） | 0% | 41.7 天 | 41.7 天 | 1.2 KB（實測） | 87.2 MB |
| `institutional_monthly_list` | TWSE T86 / TPEx | 269,010 | 387 | 3 列 / 20s | 540 → 394（實測） | 8% | 28.4 天 | 28.4 天 | 677.4 B（實測） | 255.2 MB |
| `financial_month_twse_list` | TWSE OpenAPI + MOPS t21sc03 | 368,877 | 319 | 5 列 / 20s | 900 → 619（實測） | 8% | 24.8 天 | 24.8 天 | 898.8 B（實測） | 433.6 MB |
| `ohlcv_monthly_list` | TWSE STOCK_DAY / TPEx | 300,941 | 710 | 3 列 / 10s | 1,080 → 799（實測） | 12% | 15.7 天 | 15.7 天 | 3.4 KB（實測） | 1.0 GB |
| `mops_quarterly_financials_quarter_list` | MOPS t163sb06/04 | 100,969 | 241 | 2 列 / 20s | 360 → 292（實測） | 11% | 14.3 天 | 14.3 天 | 1.1 KB（實測） | 134.2 MB |
| `product_revenue_filer_list` | MOPS t05st08 | 15,045 | 142 | 1 列 / 30s | 120 → 105（實測） | 7% | 5.9 天 | 5.9 天 | 1.8 KB（實測） | 29.8 MB |
| `company_valuation_monthly_list` | TWSE BWIBBU_d（與上列共用日 cache） | 187,287 | 1,232 | 5 列 / 10s | 1,800 → 1,331（實測） | 14% | 5.8 天 | 5.8 天 | 804.7 B（實測） | 169.6 MB |
| `financial_analysis_year_list` | MOPS t51sb02 | 27,505 | 217 | 2 列 / 20s | 360 → 322（實測） | 4% | 3.5 天 | 3.5 天 | 1.7 KB（實測） | 55.5 MB |
| `dividend_event_yfinance_list` | yfinance | 22,372 | 321 | 3 列 / 20s | 540 → 461（實測） | 9% | 47.8 小時 | 47.8 小時 | 918.7 B（實測） | 25.6 MB |
| `company_finmind_list` | FinMind | 2,250 | 472 | 1 列 / 20s | 180 → 158（實測） | 5% | 11.3 小時 | 11.3 小時 | 1.5 KB（實測） | 3.7 MB |
| `financial_quarter_yfinance_list` | yfinance | 5,612 | 631 | 3 列 / 10s | 1,080 → 821（實測） | 13% | 6.1 小時 | 6.1 小時 | 895.8 B（實測） | 5.8 MB |
| `company_list` | TWSE/TPEx OpenAPI + 經濟部 + yfinance | 2,250 | 914 | 4 列 / 20s | 720 → 540（實測） | 18% | 2.5 小時 | 2.5 小時 | 4.1 KB（實測） | 9.5 MB |
| `company_basic_info_list` | —（純 SQL 正規化） | 1,859 | 755 | 100 列 / 10s | 36,000 → 446（實測） | 0% | 2.5 小時 | 2.5 小時 | — | 1.3 MB |
| `financial_year_yfinance_list` | yfinance | 1,859 | 565 | 3 列 / 10s | 1,080 → 799（實測） | 14% | 1.6 小時 | 2.5 小時 | 859.8 B（實測） | 1.8 MB |
| `product_revenue_filer_scope_list` | MOPS ajax_t05st08_all | 122 | 122 | — | — | — | 0 分 | 0 分 | 1.6 KB（實測） | 224.0 KB |
| `chain_list` | IC 產業鏈 | 47 | 47 | 8 列 / 86400s | 0 | — | 0 分 | 0 分 | 1000.0 B（推估） | 50.5 KB |
| `market_valuation_date_list` | TWSE BWIBBU_d | 115 | 115 | 32 列 / 60s | 1,920 | 0% | 0 分 | 0 分 | 1.0 KB（實測） | 141.3 KB |
| `chain_info_list` | —（純 SQL 展開） | 6,889 | 6,889 | — | — | — | 0 分 | 0 分 | — | 1.7 MB |
| `dividend_event_list` | FinMind | 24,769 | 41 | — | — | — | 未排程 | 未排程 | 900.0 B（推估） | 23.6 MB |
| `sbl_history_monthly_list` | TWSE t13sa870 | 362,947 | 50 | — | — | — | 未排程 | 未排程 | 819.2 B（實測） | 510.4 MB |
| `foreign_ownership_monthly_list` | TWSE MI_QFIIS | 372,217 | 2 | — | — | — | 未排程 | 未排程 | 900.0 B（推估） | 355.0 MB |
| `financial_month_list` | FinMind | 522,155 | 0 | — | — | — | 未排程 | 未排程 | 900.0 B（推估） | 498.0 MB |
| `finmind_news_monthly_list` | FinMind | 157,975 | 95 | — | — | — | 未排程 | 未排程 | 13.6 KB（實測） | 2.1 GB |

**填滿後 pop 總存放估計**：約 5.7 GB（目前實際 16.7 MB）

- `finmind_news_monthly_list`（95 列）與 `dividend_event_list`（41 列）沒有 cron 排程，pop 內的列為排程以外寫入；`finmind_news` 每月平均約 20 則新聞，每 seed 列實測約 13.6 KB，是填滿後存放的最大項（約 2.1 GB）。

### 排程與限流配置（2026-11-23）

所有排程經 MCP `crawl_set_schedule` 設定。原則：每個上游資料源的總請求量留安全餘裕；backend 有磁碟 / 記憶體 cache 的來源（每列幾乎不打上游）可以開快，每列必打一次上游的來源依限流壓速。

| 上游 | 已知限流 | backend cache 粒度 | 使用的 seed（列 / 秒） | 上游請求量估計 |
| --- | --- | --- | --- | --- |
| FinMind（免費層、無 token） | 300 req/hr，超過回 402 且該列被固定成 found=false | 1 小時，key 含 as_of 窗口 → 每列 1 次 | `company_finmind_list` 1/20s、`financial_year_list` 1/60s | 240/hr（留 20%） |
| TWSE www.twse.com.tw | 非官方：約每 5 秒 3 次，超過會暫時封 IP | STOCK_DAY / tradingStock 已結束月份磁碟 cache；BWIBBU_d / T86 依日期磁碟 cache | `ohlcv_monthly_list` 3/10s、`company_valuation_monthly_list` 5/10s、`institutional_monthly_list` 3/20s、`foreign_ownership_monthly_list` 2/20s、`sbl_history_monthly_list` 1/20s | 約 10/min（日 K 每列 1 次；修正前因 IMMV 重複呼叫每列 2 次） |
| MOPS（mopsov） | 無公開保證，過頻會回「查詢過於頻繁」 | t163sb06/04、t51sb02 依（市場, 年[, 季]）磁碟 cache；t21sc03 依（市場, 年, 月）24h | `mops_quarterly_*` 2/20s、`financial_analysis_year_list` 2/20s、`financial_month_twse_list` 5/20s、`product_revenue_filer_list` 1/30s | cache 暖後每分鐘個位數 |
| yfinance | 寬鬆 | 無 | `financial_year_yfinance_list` 3/10s、`financial_quarter_yfinance_list` 3/10s、`dividend_event_yfinance_list` 3/20s | 約 40/min |

**刻意不排程**：

- `financial_month_list`、`dividend_event_list`、`finmind_news_monthly_list`（FinMind）：免費層額度已被上表用滿。`dividend_event_list` 可在 `company_finmind_list` 爬完（約 17 小時）後接手它的額度；`financial_month_list` 52 萬列在免費層需 70 天以上，且資料與 `financial_month_twse_list` 重疊，建議申請 FinMind token（600 req/hr）後再開。
- ~~`foreign_ownership_monthly_list`、`sbl_history_monthly_list`~~ 已於 2026-11-23 修正並重新排程。原本 backend 回溯沒有上限（MI_QFIIS 逐日回溯到 2004-02-11、SBL 以 31 日視窗回溯到 2005-01-28），seed 第一列又常落在掛牌前，一列可能打上千次 TWSE（實測 20 分鐘累積 1,694 個 MI_QFIIS 日檔）而暫停。已修正：backend 預設最多回溯 31 天（`max_lookback_days`，endpoint 可調）、MI_QFIIS 遇到「有資料但無該股」的交易日即停（上櫃股不再白打）；seed 起點改為「掛牌日之後第一個 5 日」（舊版 412 / 377 列落在掛牌前、379 列早於 SBL_MIN_DATE）。 修正後 5 分鐘實測：外資持股約 1.4 次 MI_QFIIS / 列（日檔跨股共用，會隨進度下降）、借券剛好 1 次 t13sa870 / 列，tick 失敗率 0–7%。

### 對作業方針的意義

1. **IMMV 重複呼叫已修正（2026-11-23），tick 失敗率從 30–70% 降到 4–18%**。原因：`pipeline._get_matview_select_sqls` 把非 seed 上游內嵌進每個 IMMV，http 呼叫被展開到每個欄位參照與每個 delta 分支；pg_ivm 以固定 snapshot 跑維護查詢，函式剛寫進 `job_control.http_cache` 的列在同一次維護中看不到，同一 URL 在一個 tick 內被打上百次（實測 `1604/business-items` 20 秒內 140+ 次），把本機 API 的 CPU 打滿（8 核 load 約 12），大量 tick 因連線逾時整批回滾。修正：
    - `custom.http_get_content_logged` 加上交易內記憶（transaction-local GUC，不受 MVCC snapshot 影響）：每 URL 請求數 10.2 → 1.84，總請求數約降為 1/3。
    - 剩下的 2 倍來自 `raw_X` 與正規化 `X` 兩個 IMMV 各呼叫一次（pg_ivm 每個 IMMV 維護結束會還原 GUC），多半命中 backend cache；唯一沒有 backend cache 的逐股月 K（TWSE STOCK_DAY / TPEx tradingStock）已加上「已結束月份」的磁碟 cache，第二次呼叫不再打上游。
    - 結果：實測速率達名目的 70–90%（修正前 1/3–1/2）；`financial_month_twse_list` 113 → 25 天、`ohlcv_monthly_list` 32 → 16 天、`company_valuation_monthly_list` 16 → 6 天。
2. **目前最長的是 FinMind `financial_year_list`（約 42 天）**，受 60 req/hr 配額限制。`company_finmind_list` 約 11 小時後爬完，可把它的 180 req/hr 轉給 `financial_year_list` / `dividend_event_list`。
3. **TWSE 系列（三大法人 28 天、月營收 25 天、日 K 16 天）仍有加速空間**：零筆 402 / 429，且日 K 修正後每列只打一次 TWSE，實際請求量約為原本的一半，可逐步調高 `ohlcv_monthly_list` / `institutional_monthly_list` 的 row_cnt 並觀察 `http_stats` 與 `[source-error]`。
4. **`foreign_ownership_monthly_list` / `sbl_history_monthly_list` 已恢復**（回溯上限修正後，見上節）。以 2 / 1 列每 20 秒保守起步，母體約 37 萬 / 36 萬列，此速率下各需約 40 天以上；TWSE 總量仍有餘裕，可觀察 `[source-error]` 後逐步調高。
5. **存放面**：填滿後約 5.7 GB，`finmind_news`（約 2.1 GB）與日 K（約 1 GB）為大宗，對 SSD 不是問題。
6. **主機睡眠會暫停爬取**（Docker VM 隨之暫停），表中填滿時間為主機持續運作下的估計；長時間爬取建議 `caffeinate -s`。

### 重新估算

```bash
# repo root；ESTIMATE_OBSERVE_MINUTES 為實測速率窗口（預設 30 分）
ESTIMATE_OBSERVE_MINUTES=60 .venv/bin/python db/poc/scripts/estimate_seed_fill_and_storage.py
```

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
11. [company_basic_info_list](#company_basic_info_list)
11a. [company_finmind_list](#company_finmind_list)
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
27. [dividend_event_yfinance_list](#dividend_event_yfinance_list)
28. [raw_dividend](#raw_dividend)
29. [dividend](#dividend)
30. [raw_dividend_yfinance](#raw_dividend_yfinance)
31. [dividend_yfinance](#dividend_yfinance)
32. [financial_quarter_list](#financial_quarter_list)
33. [financial_quarterly](#financial_quarterly)
34. [financial_quarter_yfinance_list](#financial_quarter_yfinance_list)
35. [raw_quarterly_financials_yfinance](#raw_quarterly_financials_yfinance)
36. [financial_quarterly_yfinance](#financial_quarterly_yfinance)
37. [ohlcv_monthly_list](#ohlcv_monthly_list)
38. [raw_ohlcv_monthly](#raw_ohlcv_monthly)
39. [ohlcv_daily](#ohlcv_daily)
40. [mops_quarterly_financials_quarter_list](#mops_quarterly_financials_quarter_list)
41. [raw_mops_quarterly_financials](#raw_mops_quarterly_financials)
42. [mops_quarterly_financials](#mops_quarterly_financials)
43. [financial_analysis_year_list](#financial_analysis_year_list)
44. [raw_financial_analysis](#raw_financial_analysis)
45. [financial_analysis](#financial_analysis)
46. [foreign_ownership_monthly_list](#foreign_ownership_monthly_list)
47. [raw_foreign_ownership](#raw_foreign_ownership)
48. [foreign_ownership](#foreign_ownership)
49. [sbl_history_monthly_list](#sbl_history_monthly_list)
50. [raw_sbl_history](#raw_sbl_history)
51. [sbl_history](#sbl_history)
52. [institutional_monthly_list](#institutional_monthly_list)
53. [raw_institutional_net_buy_sell](#raw_institutional_net_buy_sell)
54. [institutional_net_buy_sell](#institutional_net_buy_sell)
55. [market_valuation_date_list](#market_valuation_date_list)
56. [raw_market_valuation_summary](#raw_market_valuation_summary)
57. [market_valuation_summary](#market_valuation_summary)
58. [company_valuation_monthly_list](#company_valuation_monthly_list)
59. [raw_company_valuation](#raw_company_valuation)
60. [company_valuation](#company_valuation)
61. [finmind_news_monthly_list](#finmind_news_monthly_list)
62. [raw_finmind_news](#raw_finmind_news)
63. [finmind_news](#finmind_news)

---

## v0.0.11 — 補齊 8 個先前未被 PoC SQL 覆蓋的 endpoint

> 完全遵守「不動既有 sqls，僅新增」原則。以下 8 條新 chain 各自 `_list → raw_ → 正規化 view` 三段式，
> 上游全部掛在既有 `company_basic_info_list`（或全新的最上層 `_list`，市場層級 endpoint）之下。

| Endpoint | `_list.sql`（seed） | `raw_*.sql` | 正規化 view |
| --- | --- | --- | --- |
| `GET /company/{id}/quarterly-financials`（MOPS t163sb06+t163sb04） | [mops_quarterly_financials_quarter_list](#mops_quarterly_financials_quarter_list) | [raw_mops_quarterly_financials](#raw_mops_quarterly_financials) | [mops_quarterly_financials](#mops_quarterly_financials) |
| `GET /v1/fundamentals/financial-analysis`（MOPS t51sb02，年報） | [financial_analysis_year_list](#financial_analysis_year_list) | [raw_financial_analysis](#raw_financial_analysis) | [financial_analysis](#financial_analysis) |
| `GET /company/{id}/foreign-ownership`（TWSE MI_QFIIS） | [foreign_ownership_monthly_list](#foreign_ownership_monthly_list) | [raw_foreign_ownership](#raw_foreign_ownership) | [foreign_ownership](#foreign_ownership) |
| `GET /company/{id}/sbl-history`（TWSE SBL t13sa870） | [sbl_history_monthly_list](#sbl_history_monthly_list) | [raw_sbl_history](#raw_sbl_history) | [sbl_history](#sbl_history) |
| `GET /institutional-net-buy-sell`（TWSE T86 / TPEx dailyTrade） | [institutional_monthly_list](#institutional_monthly_list) | [raw_institutional_net_buy_sell](#raw_institutional_net_buy_sell) | [institutional_net_buy_sell](#institutional_net_buy_sell) |
| `GET /market-valuation-summary`（TWSE BWIBBU_d，市場彙總） | [market_valuation_date_list](#market_valuation_date_list) | [raw_market_valuation_summary](#raw_market_valuation_summary) | [market_valuation_summary](#market_valuation_summary) |
| `GET /company-valuation`（TWSE BWIBBU_d，單股展開） | [company_valuation_monthly_list](#company_valuation_monthly_list) | [raw_company_valuation](#raw_company_valuation) | [company_valuation](#company_valuation) |
| `GET /company/{id}/news/finmind`（FinMind TaiwanStockNews） | [finmind_news_monthly_list](#finmind_news_monthly_list) | [raw_finmind_news](#raw_finmind_news) | [finmind_news](#finmind_news) |

**設計取捨**：

- 除 `mops_quarterly_financials_quarter_list`（季頻，rule 14）與 `financial_analysis_year_list`（年頻，rule 20 分流）外，
  其餘 6 條皆是「交易日頻連續資料」或「無歷史母體 endpoint 的事件資料」，PoC 階段依 rule 11 精神以**每月一次**
  as_of 取樣（而非逐日），對有 as_of 回溯 fallback 的 endpoint（foreign-ownership、sbl-history）此取樣完全不遺漏
  代表性資料；對無 fallback 的 endpoint（institutional-net-buy-sell、market-valuation-summary、company-valuation、
  news/finmind）則是 PoC 範圍內的代表性抽樣（未來如需提高密度，可額外新增「事件母體」endpoint 走完整 rule 15）。
- **爬取效率（cronjob 大量拉資料前的重點）**：
  - `company_valuation_monthly_list` 改為直接 `CROSS JOIN` [market_valuation_date_list](#market_valuation_date_list)
    （而非各自獨立 `generate_series`），確保 `/api/company-valuation` 與 `/api/market-valuation-summary` 這兩個
    共用同一份 TWSE `BWIBBU_d` 每日 payload（backend 磁碟 cache 以日期為 key）的 endpoint，**在同一天觸發**，
    讓 cron 排程實際只需對上游打 1 次（backend cache hit），而非兩個 seed 各自取樣到不同日期造成雙倍上游成本。
  - `foreign_ownership_monthly_list` / `sbl_history_monthly_list` / `institutional_monthly_list` /
    `finmind_news_monthly_list` 的取樣錨點日一律從「每月 1 日」改為「每月 5 日」，與 `market_valuation_date_list`
    對齊慣例；對沒有 as_of 回溯 fallback 的 endpoint（institutional-net-buy-sell、news/finmind）可降低命中元旦等
    連續假期、白白浪費一次 API 呼叫卻拿到空結果的機率，長期大量 cron 拉取時可省下的無效呼叫比例更明顯。
- `market_valuation_date_list` 是本次唯一「不依賴 `company_basic_info_list`」的新 `_list`，因為
  `/api/market-valuation-summary` 是市場層級彙總（無 `stock_id` 參數）；`company_valuation_monthly_list` 反過來
  依賴它取得日期集合（見上方爬取效率說明）。
- `sbl_history` / `finmind_news` 的正規化 view 需攤平陣列（`records[]` / `items[]`），依 rule 16 使用
  `CROSS JOIN LATERAL` + `COALESCE(..., '[]'::jsonb)`，found=false 或空陣列的月份自然攤平 0 列。
- 所有新增正規化 view 的 JSON 存取一律使用 `->>`（text 抽取）再視需要 `::NUMERIC` / `custom.parse_iso_date`，
  對齊既有 `financial_yearly_yfinance` / `financial_quarterly_yfinance` 已確立的型別安全慣例（避免
  `cannot cast jsonb null to type numeric`）。
- `market_valuation_summary` 未攤平 `sample_constituents` 明細陣列（PoC 範圍聚焦市場彙總指標）；
  需要成分股層級明細請改用逐股 `company_valuation`。

---

## mops_quarterly_financials_quarter_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL 展開，以 3 個月步長直接產生季末日）
- 對應 [`/api/company/{stock_id}/quarterly-financials`](../../app/main.py) MOPS t163sb06 + t163sb04。
- 設計理念（rule 14, 20）：季頻獨立 `_list`，不與既有 FinMind/yfinance 版季度母體混用（不同資料源分流）。
- 設計理念（rule 13）：起點對齊到設立日所在季的季末月、步長 3 個月，不再逐月展開後用 WHERE 篩季末月（輸出相同，hidden 每 tick 少算 2/3 的列）。
- **Point-in-time（2026-11-25，verify_questions I002）**：下游以 `as_of_date`（該季法定公告期限）呼叫 endpoint，endpoint 只回當天已公開的季度，所以每列恰好是 `quarter_end` 那一季且當時已公告。期限：Q1 5/15、Q2 8/31（金融業期限；一般公司 8/14 已公告）、Q3 11/14、Q4 隔年 3/31（`app/pit.py`）。未到期限的季度不產生（`WHERE as_of_date <= CURRENT_DATE`，rule 13 資料邊界）。舊版以季末日取樣，每列都拿到當天尚未公告的當季（Q002 C4 73/73 違規）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `quarter_end` | DATE | 該季最後一日（3/6/9/12 月月末） | `generate_series(..., INTERVAL '3 months')` |
| `as_of_date` | DATE | 該季財報的法定公告期限（下游 endpoint 的 as_of） | 由 `quarter_end` 計算 |

---

## raw_mops_quarterly_financials

- **上游 SQL**：[mops_quarterly_financials_quarter_list](#mops_quarterly_financials_quarter_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/quarterly-financials?as_of={as_of_date}`
  - 上游：MOPS `ajax_t163sb06`（營益分析彙總）+ `ajax_t163sb04`（綜合損益表）

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `mops_quarterly_financials_quarter_list.stk_code` |
| `quarterly_financials` | JSONB | endpoint 回傳整包 JSON | `custom.http_get_content(url)` |
| `as_of` | DATE | 查詢基準日（= 該季公告期限 `as_of_date`） | `mops_quarterly_financials_quarter_list.as_of_date` |

---

## mops_quarterly_financials

- **上游 SQL**：[raw_mops_quarterly_financials](#raw_mops_quarterly_financials)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `MopsQuarterlyFinancialsResponse` + `MopsQuarterlyFinancialsData`。
- **數值為單季**（2026-11-25 修正，verify_questions I001）：MOPS t163sb06／t163sb04 是年初至今累計，endpoint 的 `data` 已換算成單季（Q2–Q4 = 本季累計 − 前一季累計，比率先還原成金額再相減）；原始累計值在 raw JSON 的 `data_ytd`。
- 金融業不在 t163sb06，只有 `eps`，營收與比率為 NULL（I009）。
- 主鍵 `as_of` 取 raw 的 seed 日期（該季公告期限），不取回應 JSON（rule 7，I004）。
- Point-in-time（I002）：`data_date` = 季末日、`available_date` = 法定公告期限，endpoint 保證 `available_date <= as_of`。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_mops_quarterly_financials.stk_code` |
| `as_of` | DATE | 查詢基準日（seed 公告期限日） | `raw_mops_quarterly_financials.as_of` |
| `stock_id` | TEXT | 股票代號 | `quarterly_financials.stock_id` |
| `company_name` | TEXT | 公司名稱 | `quarterly_financials.company_name` |
| `market` | TEXT | `sii` / `otc` | `quarterly_financials.market` |
| `data_date` | DATE | 實際季度結束日 | `quarterly_financials.data_date` |
| `available_date` | DATE | 該季財報的法定公告期限（≤ `as_of`） | `quarterly_financials.available_date` |
| `fiscal_year` | NUMERIC | 財報西元年度 | `quarterly_financials.fiscal_year` |
| `quarter` | NUMERIC | 財報季度 1~4 | `quarterly_financials.quarter` |
| `revenue_millions` | NUMERIC | 單季營業收入（百萬元） | `quarterly_financials.data.revenue_millions` |
| `gross_margin_pct` | NUMERIC | 毛利率 (%) | `quarterly_financials.data.gross_margin_pct` |
| `operating_margin_pct` | NUMERIC | 營業利益率 (%) | `quarterly_financials.data.operating_margin_pct` |
| `pretax_margin_pct` | NUMERIC | 稅前純益率 (%) | `quarterly_financials.data.pretax_margin_pct` |
| `net_margin_pct` | NUMERIC | 稅後純益率 (%) | `quarterly_financials.data.net_margin_pct` |
| `eps` | NUMERIC | 單季基本每股盈餘（元） | `quarterly_financials.data.eps` |

---

## financial_analysis_year_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series` 整數年）
- 對應 [`/api/v1/fundamentals/financial-analysis`](../../app/main.py) MOPS t51sb02。
- 設計理念（rule 20）：年頻但資料源為 MOPS（非 FinMind/yfinance），獨立分流，避免與
  `financial_year_list` / `financial_year_yfinance_list` 混用同一 seed。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `year` | INT | 查詢西元年（≥ 2012） | `generate_series(GREATEST(incorporation_year, 2012), year(今天 − 4 個月) − 1)`：年報隔年才公布，每年 5 月起才納入前一年度（I008） |

---

## raw_financial_analysis

- **上游 SQL**：[financial_analysis_year_list](#financial_analysis_year_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/v1/fundamentals/financial-analysis?stock_id={stk_code}&year={year}`
  - 上游：MOPS 財務分析彙整表 `t51sb02`

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_analysis_year_list.stk_code` |
| `year` | INT | 查詢西元年 | `financial_analysis_year_list.year` |
| `financial_analysis` | JSONB | endpoint 回傳整包 JSON | `custom.http_get_content(url)` |

---

## financial_analysis

- **上游 SQL**：[raw_financial_analysis](#raw_financial_analysis)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `FinancialAnalysisResponse` + `FinancialAnalysisData`（t51sb02 21 欄財務比率）。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_financial_analysis.stk_code` |
| `year` | INT | 財報西元年度 | `raw_financial_analysis.year` |
| `stock_id` | TEXT | 股票代號 | `financial_analysis.stock_id` |
| `reason` | TEXT | `found=false` 原因 | `financial_analysis.reason` |
| `company_name` | TEXT | 公司名稱 | `financial_analysis.data.company_name` |
| `market` | TEXT | `sii` / `otc` | `financial_analysis.data.market` |
| `debt_ratio` … `cash_reinvestment_ratio` | NUMERIC | t51sb02 其餘 19 個財務比率欄位 | `financial_analysis.data.*` |

---

## foreign_ownership_monthly_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 對應 [`/api/company/{stock_id}/foreign-ownership`](../../app/main.py) 上市 TWSE MI_QFIIS／上櫃 TPEx qfii（I006）。
- 設計理念（rule 11, 15 退化特例）：交易日頻連續資料 + 有回溯 fallback，每月一次取樣即可。
- 邊界（rule 13）：上櫃股起點下限 2007-05-05（TPEx qfii 實測最早有資料日 2007-05-02），上市股 2004-02-05。
- 爬取效率：取樣錨點日固定在每月 5 日（而非 1 日），降低命中元旦等長假的機率。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `month_start_date` | DATE | 每月 5 日（從 `GREATEST(掛牌日之後第一個 5 日, 2004-02-05；上櫃 2007-05-05)` 起；2004-02 那列改用下限 2004-02-11） | `generate_series(...)` |

---

## raw_foreign_ownership

- **上游 SQL**：[foreign_ownership_monthly_list](#foreign_ownership_monthly_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/foreign-ownership?as_of={month_start_date}`
  - 上游：上市 TWSE 外資及陸資投資持股統計 `MI_QFIIS`；上櫃 TPEx 僑外資及陸資持股比例 `qfii`（無陸資上限與異動日期，對應欄位為 NULL）

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `foreign_ownership_monthly_list.stk_code` |
| `foreign_ownership` | JSONB | endpoint 回傳整包 JSON | `custom.http_get_content(url)` |
| `as_of` | DATE | 查詢基準日 | `foreign_ownership_monthly_list.month_start_date` |

---

## foreign_ownership

- **上游 SQL**：[raw_foreign_ownership](#raw_foreign_ownership)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `ForeignOwnershipResponse` + `ForeignOwnershipRow`。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_foreign_ownership.stk_code` |
| `as_of` | DATE | 查詢基準日 | `foreign_ownership.as_of` |
| `stock_id` | TEXT | 股票代號 | `foreign_ownership.stock_id` |
| `data_date` | DATE | 實際資料日期（可能早於 as_of） | `foreign_ownership.data_date` |
| `stock_name` | TEXT | 證券名稱 | `foreign_ownership.row.stock_name` |
| `issued_shares` … `china_investment_limit_ratio_pct` | NUMERIC | `ForeignOwnershipRow` 其餘數值欄位 | `foreign_ownership.row.*` |
| `change_reason` | TEXT | 與前日異動原因 | `foreign_ownership.row.change_reason` |
| `last_change_date` | DATE | 最近一次申報異動日期 | `foreign_ownership.row.last_change_date` |

---

## sbl_history_monthly_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 對應 [`/api/company/{stock_id}/sbl-history`](../../app/main.py) TWSE SBL t13sa870。
- 設計理念（rule 11, 15 退化特例）：backend 以 31 日視窗（`[as_of − 30, as_of]`）回溯，每月取樣一次，不打過密日期。相鄰取樣只差 28–31 天，視窗會重疊 1–3 天；重疊日的紀錄由下游 [sbl_history](#sbl_history) 濾掉（verify_questions I017）。
- 爬取效率：取樣錨點日固定在每月 5 日，與其餘新增 monthly seed 慣例一致。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `month_start_date` | DATE | 每月 5 日（從 `GREATEST(掛牌日之後第一個 5 日, 2005-02-05)` 起） | `generate_series(...)` |

---

## raw_sbl_history

- **上游 SQL**：[sbl_history_monthly_list](#sbl_history_monthly_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/sbl-history?as_of={month_start_date}`
  - 上游：TWSE 借券歷史還券明細 `t13sa870`

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `sbl_history_monthly_list.stk_code` |
| `sbl_history` | JSONB | endpoint 回傳整包 JSON（含 `records[]`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 查詢基準日 | `sbl_history_monthly_list.month_start_date` |

---

## sbl_history

- **上游 SQL**：[raw_sbl_history](#raw_sbl_history)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `SblHistoryResponse` + `SblHistoryRecord`；`CROSS JOIN LATERAL` 攤平 `records[]`。
- 去重（verify_questions I017）：只保留 `completion_date > as_of − 1 個月` 的紀錄，讓每筆還券紀錄只歸屬一個 as_of（上游視窗重疊造成的重複，2026-11-27 實測 296 列）。WHERE 是單純比較、沒有 subquery，符合 rule 16。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_sbl_history.stk_code` |
| `as_of` | DATE | 查詢基準日 | `sbl_history.as_of` |
| `stock_id` | TEXT | 股票代號 | `sbl_history.stock_id` |
| `data_date` | DATE | 實際找到的最近完成還券日期 | `sbl_history.data_date` |
| `transaction_date` | DATE | 借券成交日期 | `sbl_history.records[].transaction_date` |
| `stock_name` | TEXT | 證券名稱 | `sbl_history.records[].stock_name` |
| `transaction_type` | TEXT | 交易方式 | `sbl_history.records[].transaction_type` |
| `quantity_lots` | NUMERIC | 成交數量 | `sbl_history.records[].quantity_lots` |
| `fee_rate_pct` | NUMERIC | 成交費率 (%) | `sbl_history.records[].fee_rate_pct` |
| `completion_close_price` | NUMERIC | 完成還券日收盤價 | `sbl_history.records[].completion_close_price` |
| `completion_date` | DATE | 完成還券日期 | `sbl_history.records[].completion_date` |
| `lending_days` | NUMERIC | 借券天數 | `sbl_history.records[].lending_days` |

---

## institutional_monthly_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 對應 [`/api/institutional-net-buy-sell`](../../app/main.py) TWSE T86 / TPEx dailyTrade。
- 設計理念（rule 11）：交易日頻連續資料、無回溯 fallback，PoC 每月一次代表性取樣。
- 爬取效率：本 endpoint 無 fallback，取樣錨點日固定在每月 5 日以降低命中假日、拿到空結果的機率。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `month_start_date` | DATE | 每月 5 日（從 `GREATEST(listing_date, 2012-05-02)` 起） | `generate_series(...)` |

---

## raw_institutional_net_buy_sell

- **上游 SQL**：[institutional_monthly_list](#institutional_monthly_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/institutional-net-buy-sell?stk_code={stk_code}&date={month_start_date}`
  - 上游：TWSE 三大法人買賣超日報 `T86` / TPEx `dailyTrade`

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `institutional_monthly_list.stk_code` |
| `institutional_net_buy_sell` | JSONB | endpoint 回傳整包 JSON | `custom.http_get_content(url)` |
| `as_of` | DATE | 查詢基準日 | `institutional_monthly_list.month_start_date` |

---

## institutional_net_buy_sell

- **上游 SQL**：[raw_institutional_net_buy_sell](#raw_institutional_net_buy_sell)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `InstitutionalNetBuySellResponse` + `InstitutionalNetBuySellRow`。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_institutional_net_buy_sell.stk_code` |
| `as_of` | DATE | 查詢基準日（seed 取樣日；回應失敗時仍有值，I004）。實際交易日見 `trade_date` | `raw_institutional_net_buy_sell.as_of` |
| `market` | TEXT | `上市` / `上櫃` | `institutional_net_buy_sell.market` |
| `trade_date` | DATE | 明細所屬交易日 | `institutional_net_buy_sell.row.trade_date` |
| `stock_name` | TEXT | 股票簡稱 | `institutional_net_buy_sell.row.stock_name` |
| `foreign_investors_net_buy_sell` … `total_institutional_net_buy_sell` | NUMERIC | `InstitutionalNetBuySellRow` 其餘買賣超股數欄位 | `institutional_net_buy_sell.row.*` |

---

## market_valuation_date_list

- **上游 SQL**：無（最上層 `_list`）
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 對應 [`/api/market-valuation-summary`](../../app/main.py) TWSE BWIBBU_d（市場層級彙總，無 stock_id 維度）。
- 設計理念（rule 11）：單日全市場彙總、無回溯 fallback，PoC 每月一次（每月 5 日）代表性取樣。
- **邊界（rule 13）**：起點 2017-05-05。TWSE BWIBBU_d 雖自 2005-09-02 起有資料，但 2017-04-05 以前 payload 只有 5 欄（無收盤價），backend 依 8 欄格式解析而失敗；實測 pop 2017-05 以前 139 列全部 NULL。backend 支援舊格式後可往回調，並同步調整 [company_valuation_monthly_list](#company_valuation_monthly_list)。
- **已知限制**：5 日落在週末 / 假日時無回溯，2017-05 以後約 1/3 列為 NULL，需 backend 加回溯才能解。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `valuation_date` | DATE | 每月 5 日（從 2017-05-05 起） | `generate_series(...)` |

---

## raw_market_valuation_summary

- **上游 SQL**：[market_valuation_date_list](#market_valuation_date_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/market-valuation-summary?date={valuation_date}&sample_size=5`
  - 上游：TWSE 收盤後個股本益比、殖利率及股價淨值比 `BWIBBU_d`

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `valuation_date` | DATE | 查詢日 | `market_valuation_date_list.valuation_date` |
| `market_valuation_summary` | JSONB | endpoint 回傳整包 JSON | `custom.http_get_content(url)` |

---

## market_valuation_summary

- **上游 SQL**：[raw_market_valuation_summary](#raw_market_valuation_summary)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `MarketValuationSummaryResponse` + `MarketValuationSummary`（彙總層級，未含 `sample_constituents` 明細）。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `valuation_date` | DATE | 查詢日 | `raw_market_valuation_summary.valuation_date` |
| `as_of` | DATE | 查詢日（= seed `valuation_date`，I004） | `raw_market_valuation_summary.valuation_date` |
| `market_scope` | TEXT | 市場範圍描述 | `market_valuation_summary.market_scope` |
| `calculation_method` | TEXT | 計算方法標記 | `market_valuation_summary.calculation_method` |
| `total_market_cap` … `excluded_no_shares` | NUMERIC | `MarketValuationSummary` 其餘 16 個彙總欄位 | `market_valuation_summary.summary.*` |

---

## company_valuation_monthly_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 對應 [`/api/company-valuation`](../../app/main.py) 上市 TWSE BWIBBU_d／上櫃 TPEx peQryDate + dailyQuotes（單股展開；上櫃 2026-11-25 起支援，I005）。
- **爬取效率**：取樣日格點與 [market_valuation_date_list](#market_valuation_date_list) **完全相同**（起點 2017-05-05、每月 5 日），
  兩個 endpoint 共用同一份 TWSE `BWIBBU_d` 每日 payload（backend 依日期磁碟 cache），對齊取樣日期後
  同一天只會打上游 1 次。若調整 `market_valuation_date_list` 的起點，這裡要同步改。
- **設計理念（rule 13）**：掛牌日邊界直接寫進 `generate_series` 起點（`GREATEST(2017-05-05, 掛牌日當月 5 日；掛牌日晚於 5 日則下月 5 日)`），
  不再 `CROSS JOIN` 全部日期後用 `WHERE valuation_date >= listing_date` 篩。輸出與舊版相同（實測 85,555 列，雙向 `EXCEPT ALL` 皆 0）。
  `WHERE listing_date IS NOT NULL` 為技術性 guard（rule 13 例外）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `valuation_date` | DATE | 掛牌後每月 5 日（格點與 `market_valuation_date_list` 相同） | `generate_series(...)` |

---

## raw_company_valuation

- **上游 SQL**：[company_valuation_monthly_list](#company_valuation_monthly_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company-valuation?stock_id={stk_code}&date={valuation_date}`
  - 上游：上市 TWSE `BWIBBU_d`；上櫃 TPEx `peQryDate`（本益比 / 殖利率 / 股價淨值比）+ `dailyQuotes`（收盤價），2007-01-02 起

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_valuation_monthly_list.stk_code` |
| `company_valuation` | JSONB | endpoint 回傳整包 JSON | `custom.http_get_content(url)` |
| `as_of` | DATE | 查詢基準日 | `company_valuation_monthly_list.valuation_date` |

---

## company_valuation

- **上游 SQL**：[raw_company_valuation](#raw_company_valuation)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `CompanyValuationResponse` + `MarketValuationConstituent`。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_company_valuation.stk_code` |
| `as_of` | DATE | 查詢日（seed 日期；回應失敗時仍有值，I004） | `raw_company_valuation.as_of` |
| `stock_id` | TEXT | 股票代號 | `company_valuation.stock_id` |
| `reason` | TEXT | `found=false` 原因 | `company_valuation.reason` |
| `calculation_method` | TEXT | 計算方法標記 | `company_valuation.calculation_method` |
| `stock_name` | TEXT | 股票名稱 | `company_valuation.constituent.stock_name` |
| `close_price` … `dps` | NUMERIC | `MarketValuationConstituent` 其餘反推數值欄位 | `company_valuation.constituent.*` |

---

## finmind_news_monthly_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 對應 [`/api/company/{stock_id}/news/finmind`](../../app/main.py) FinMind `TaiwanStockNews`。
- 設計理念（rule 15 附註）：新聞為事件/文字資料且無 interpolation、無歷史母體 endpoint，
  PoC 階段每月一次代表性取樣（已知會漏掉大部分無新聞月份，未來可補「新聞歷史」事件母體 endpoint）。
- 爬取效率：本 endpoint 無回溯 fallback，取樣錨點日固定在每月 5 日以降低命中元旦等連續假期的機率。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `month_start_date` | DATE | 每月 5 日（從 `GREATEST(listing_date, 2019-01-01)` 起） | `generate_series(...)` |

---

## raw_finmind_news

- **上游 SQL**：[finmind_news_monthly_list](#finmind_news_monthly_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/news/finmind?as_of={month_start_date}`
  - 上游：FinMind v4 `TaiwanStockNews`

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `finmind_news_monthly_list.stk_code` |
| `finmind_news` | JSONB | endpoint 回傳整包 JSON（含 `items[]`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 查詢基準日 | `finmind_news_monthly_list.month_start_date` |

---

## finmind_news

- **上游 SQL**：[raw_finmind_news](#raw_finmind_news)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `FinMindNewsResponse` + `FinMindNewsItem`；`CROSS JOIN LATERAL` 攤平 `items[]`。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_finmind_news.stk_code` |
| `as_of` | DATE | 查詢基準日（seed 日期，I004） | `raw_finmind_news.as_of` |
| `stock_id` | TEXT | 股票代號 | `finmind_news.stock_id` |
| `data_date` | DATE | 實際回傳新聞日期 | `finmind_news.data_date` |
| `news_date` | DATE | 該則新聞發布日期 | `finmind_news.items[].date` |
| `title` | TEXT | 新聞標題 | `finmind_news.items[].title` |
| `source` | TEXT | 新聞來源媒體 | `finmind_news.items[].source` |
| `link` | TEXT | 原始新聞連結 | `finmind_news.items[].link` |

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
- **HTTP API endpoint**：無（純 SQL `GROUP BY stk_code`，rule 2）
- **設計理念**：只讀已物化的 `pop.chain_info_list`（rule 21），所以 `hidden.company_list` 每次 cron tick 都是純 SQL、不會重打 47 條 `/api/chain/*`。以 `stk_code` 為唯一 key（rule 6），同一公司在不同產業鏈名稱不一致時取 `MIN(company_name)`，避免同代號兩列讓下游 `raw_*` 重複發查。本表是 seed，不經 pg_ivm 展開，`GROUP BY` 不受 rule 16 限制。
- **曾經的寫法**：commit `45ecf3f` 為了讓 poc `LIMIT 1` 不展開全部產業鏈，改成以 recursive CTE 直接呼叫 `/api/chain/*`，違反 rule 2，也讓 pop 的 cron 每 tick 重打 API；現在 API 回應有 `job_control.http_cache`，poc 全展開冷啟動約 0.4 秒，已無此必要。

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

## company_basic_info_list

- **上游 SQL**：[raw_company_info](#raw_company_info)
- **HTTP API endpoint**：無（純 JSON 攤平）
- **角色**：seed（`_list`）— 每檔股票一列。作為多條 `_list` seed（`financial_year_list` / `financial_year_yfinance_list` / `financial_month_list` / `financial_month_twse_list` / `ohlcv_monthly_list`）的共同上游。
- **命名歷史**：poc 層 seed 統一以 `_list` 後綴，原名 `company_basic_info` 已改為 `company_basic_info_list` 以符合此慣例。

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

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- 以 `GREATEST(custom.trunc_year(incorporation_date), 1992-01-01)` 為起點,`generate_series(..., CURRENT_DATE, INTERVAL '1 year')` 展開出每一個年初
- **邊界（rule 13）**：FinMind `TaiwanStockFinancialStatements` 最早一季為 1991-12-31（2026-11 實測 2330），更早的 as_of 必定回空。`incorporation_date IS NOT NULL` 為技術性 guard。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `year_start_date` | DATE | 公司歷年的 1 月 1 日（從 `GREATEST(設立年, 1992)` 到今年） | `generate_series(GREATEST(trunc_year(incorporation_date), '1992-01-01'), CURRENT_DATE, '1 year')` |

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

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **邊界（rule 13）**：只產生「今年 1/1」（`GREATEST(trunc_year(incorporation_date), trunc_year(CURRENT_DATE))`）。yfinance `quarterly_financials` 只保留最近幾季的滾動視窗，實測 pop 300 列中只有最新一年的列帶回季度、更早年度全是空陣列；每跨一年自然多一列，已爬過的舊列留在 pop。
- **設計理念（rule 20）**：結構同 `financial_year_list`（只差上述下限）,拆成兩張目的在於「分流爬取」：`raw_yearly_financials`（FinMind）與 `raw_yearly_financials_yfinance` 對應到不同 `pop.<seed>` 空表，可各自以不同的 doubling limit 進度填充。yfinance 限流寬（每小時可數千次），可以先拉高 LIMIT 快速拉齊；FinMind 慢，保持保守進度，兩邊互不阻塞。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `year_start_date` | DATE | 今年 1 月 1 日（yfinance 只有近幾季，舊年度不產生） | `generate_series(GREATEST(trunc_year(incorporation_date), trunc_year(CURRENT_DATE)), CURRENT_DATE, '1 year')` |

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
| `as_of` | DATE | 本筆指標的查詢基準日（seed 日期，I004） | `raw_yearly_financials_yfinance.as_of` |
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

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL 產生）
- **服務的下游**：[raw_monthly_revenue](#raw_monthly_revenue)（FinMind 版）
- 設計理念（規則 11, 14, 15）：月營收公布頻率為每月一次（次月 10 日前）,幾乎所有公司每月都有,規則性格點恰等於事件母體 — 為 rule 15 的退化特例。因此仍以「每月一次」的 as_of 遍歷所有月份切片,不必以日為單位密集抓取。
- **邊界（rule 13）**：起點 `GREATEST(成立月 1 日, 2002-02-01)`。FinMind `TaiwanStockMonthRevenue` 最早一筆為 2002-01 營收（date=2002-02-01，2026-11 實測 2330）。`incorporation_date IS NOT NULL` 為技術性 guard。
- **設計理念（規則 20）**：本 seed 專供 FinMind 版 raw_monthly_revenue。TWSE/MOPS 版已於 v0.0.10 五次補丁拆到獨立 seed [financial_month_twse_list](#financial_month_twse_list)。並行拆分目的在避免共用 seed 時慢端（FinMind 免費層 300 req/hr）拖累快端（TWSE OpenAPI + MOPS t21sc03,後者有 24h server-side cache,quota 寬鬆很多）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `month_start_date` | DATE | 從 `GREATEST(成立月, 2002-02)` 開始、每月 1 日 generate 一列,直到 CURRENT_DATE | `generate_series(...)` |

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
| `as_of` | DATE | 本筆指標的查詢基準日（seed 日期，I004） | `raw_monthly_revenue.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `revenue.stock_id` |
| `latest_month_label` | TEXT | 最近一個月的年/月標籤（例 `2026/04`） | `revenue.latest_month_label` |
| `latest_month_value` | NUMERIC | 最近一個月營收（新台幣元） | `revenue.latest_month_value` |
| `latest_month_yoy_pct` | NUMERIC | 該月年增率 (%) | `revenue.latest_month_yoy_pct` |
| `ttm_value` | NUMERIC | 最近 12 個完整月份營收加總（TTM） | `revenue.ttm_value` |
| `ttm_yoy_pct` | NUMERIC | TTM 營收年增率 (%) | `revenue.ttm_yoy_pct` |

---

## financial_month_twse_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **服務的下游**：[raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
- **設計理念（規則 20）**：結構同 [financial_month_list](#financial_month_list)（只差資料源下限），拆成兩張目的在於「分流爬取」：`raw_monthly_revenue`（FinMind, 免費層 300 req/hr）與 `raw_monthly_revenue_twse`（TWSE OpenAPI + MOPS t21sc03; TWSE OpenAPI 無明顯 quota、MOPS 內部有 24h server-side cache）對應到不同 `pop.<seed>` 空表，可各自以不同的 batch limit 進度填充。TWSE 端限流寬，可拉高 `batch_size.json` 的 `financial_month_twse_list` 快速拉齊（初版 4/tick，後續由 `probe_seed_insert_limit` 推升），FinMind 端保持 1/tick 不被拖累。
- **邊界（rule 13）**：起點 `GREATEST(成立月 10 日, 2010-01-10)`。MOPS t21sc03 靜態頁最早為上市 民國 98/12、上櫃 民國 99/1，更早月份 404（2026-11 實測）。`incorporation_date IS NOT NULL` 為技術性 guard。
- **Point-in-time（2026-11-25，verify_questions I002）**：取樣日 = 每月 10 日（上月營收的法定公告期限，`app/pit.py`）。endpoint 只回 as_of 當天已到期限的月份，所以每列恰好對應「上個月」，回測不會看到尚未公告的營收；舊版以每月 1 日取樣，會回傳 9 天後才公告的上月營收（Q002 C3 36/36 違規）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `as_of_date` | DATE | 從 `GREATEST(成立月, 2010-01)` 開始、每月 10 日 generate 一列，直到 CURRENT_DATE | `generate_series(...)` |

---

## raw_monthly_revenue_twse

- **上游 SQL**：[financial_month_twse_list](#financial_month_twse_list)（TWSE 專屬 seed）
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/revenue/twse?as_of={as_of_date}`
  - 上游：證交所體系雙來源。「最新一個月」：[TWSE OpenAPI](https://openapi.twse.com.tw/v1/opendata/t187ap05_L) / [TPEx OpenAPI](https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv) t187ap05;「歷史月營收」：[公開資訊觀測站 MOPS](https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_113_5_0.html) t21sc03 採用 IFRSs 後每月營業收入彙總表。
  - 與 FinMind 版同一輸出 spec,提供「官方」源頭供審計溯源,免 token、免限流。
- 設計理念（規則 20）：與 [raw_monthly_revenue](#raw_monthly_revenue) 分流爬取，各自對應獨立 `pop.<seed>` 空表。本 raw 可以拉高 batch_size 快速拉齊，不受 FinMind 300 req/hr 拖累。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `financial_month_twse_list.stk_code` |
| `revenue` | JSONB | `/revenue/twse` endpoint 整包 JSON（結構與 raw_monthly_revenue 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `as_of_date`，每月 10 日） | `financial_month_twse_list.as_of_date` |

---

## monthly_revenue_twse

- **上游 SQL**：[raw_monthly_revenue_twse](#raw_monthly_revenue_twse)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [monthly_revenue](#monthly_revenue) align,只差在資料源（TWSE/MOPS vs FinMind）
- 設計理念（規則 13）：不做 WHERE 過濾,保留 raw 母體的所有 rows。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_monthly_revenue_twse.stk_code` |
| `as_of` | DATE | 本筆指標的查詢基準日（seed 日期，每月 10 日） | `raw_monthly_revenue_twse.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `revenue.stock_id` |
| `latest_month_label` | TEXT | as_of 當天已公告的最近一個月的年/月標籤（例 `2026/04`） | `revenue.latest_month_label` |
| `data_date` | DATE | 該營收月份的月底 | `revenue.data_date` |
| `available_date` | DATE | 該月營收的法定公告期限（次月 10 日），一定 ≤ `as_of`（I002） | `revenue.available_date` |
| `latest_month_value` | NUMERIC | 最近一個月營收（新台幣元） | `revenue.latest_month_value` |
| `latest_month_yoy_pct` | NUMERIC | 該月年增率 (%) | `revenue.latest_month_yoy_pct` |
| `ttm_value` | NUMERIC | 最近 12 個完整月份營收加總（TTM） | `revenue.ttm_value` |
| `ttm_yoy_pct` | NUMERIC | TTM 營收年增率 (%) | `revenue.ttm_yoy_pct` |

---

## company_finmind_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL，內容與 `company_basic_info_list` 的上市櫃公司相同）
- **設計理念（rule 20 例子3）**：FinMind 專用的公司 seed，只餵 `raw_dividend_history`，讓 FinMind 以 ≤ 300 req/hr 獨立排程，不被 `company_list`（TWSE / yfinance 速度）拖進限流。只讀 `pop.company_basic_info_list`，所以進度永遠落在 `company_list` 之後。
- **只取上市櫃（I015）**：`company_list` 是產業鏈公司全集，含興櫃／已下市代號。資料庫的分析母體是目前上市櫃公司，股利歷史也對齊同一個母體。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `company_name` | TEXT | 公司名稱 | `company_basic_info_list.company_name` |

---

## raw_dividend_history

- **上游 SQL**：[company_finmind_list](#company_finmind_list)（rule 20：FinMind 專用 seed）
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/history`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockDividend`（20 年區間全量）
- 設計理念（rule 15）：股利是「事件性資料」— 每公司每年 0~數次除息事件。對 (公司 × 每年年初) 打 `/dividend?as_of=X` 只會回 as_of 前最後一筆,多年查詢會產生大量重複命中。本 raw 每公司一次撈整包歷史 events array,作為下游 `dividend_event_list` 的來源母體。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_finmind_list.stk_code` |
| `dividend_history` | JSONB | `/dividend/history` endpoint 整包 JSON（含 `stock_id`, `total_events`, `events[]`）;每個 event 為 `DividendSection` 結構 | `custom.http_get_content(url)` |

---

## raw_dividend_history_yfinance

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)（只取上市櫃，I015：興櫃／已下市代號在 Yahoo 沒有股利歷史，實測 187 檔全部 0 筆）
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/history/yfinance`
  - 上游：yfinance Python Library — `Ticker.dividends`（整段歷史）
- 與 `raw_dividend_history` (FinMind 版) 結構完全一致,純資料源替代版。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `dividend_history` | JSONB | `/dividend/history/yfinance` endpoint 整包 JSON（結構與 raw_dividend_history 一致） | `custom.http_get_content(url)` |

---

## dividend_event_list

- **上游 SQL**：[raw_dividend_history](#raw_dividend_history)
- **HTTP API endpoint**：無（`_list` 不呼叫 HTTP,rule 2）
- 用途：作為 `raw_dividend` 的上游母體 — 攤平該公司歷史所有股利事件 `reference_date`。
- 設計理念（rule 15）：股利屬「事件性資料」,事件母體由資料本身告訴我們（哪些日期真正有事件）,而非以規則性時間格點採樣。
- 設計理念（rule 17）：事件 key 選 `reference_date`（歷史列皆非 NULL），不選 `cash_ex_dividend_date`（純股票股利年份為 NULL），因此不需要 `WHERE ... IS NOT NULL`。
- 設計理念（rule 6）：以 `(stk_code, reference_date)` 為唯一 key。SQL 不另外去重，依賴上游 events 同公司同 `reference_date` 不重複（實測 pop 3,709 筆 events 重複 0、NULL 0）。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend_history.stk_code` |
| `reference_date` | DATE | 股利事件基準日（本次事件的 as_of） | `dividend_history.events[].reference_date` |

---

## dividend_event_yfinance_list

- **上游 SQL**：[raw_dividend_history_yfinance](#raw_dividend_history_yfinance)
- **HTTP API endpoint**：無
- 與 [dividend_event_list](#dividend_event_list) 同結構、同意圖;差別在資料源。用途：作為 `raw_dividend_yfinance` 的上游母體。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend_history_yfinance.stk_code` |
| `reference_date` | DATE | 股利事件基準日 | `dividend_history.events[].reference_date` |

---

## raw_dividend

- **上游 SQL**：[dividend_event_list](#dividend_event_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend?as_of={reference_date}`
  - 上游：[FinMind v4](https://api.finmindtrade.com/api/v4/data) dataset `TaiwanStockDividend`（透過 `_pick_dividend` 取 as_of 前最後一筆）
- 設計理念（rule 15）：以事件母體「該公司歷史真正的股利事件日」作為 as_of 遍歷。每筆 `(stk_code, reference_date)` 打一次 `/dividend?as_of=reference_date`,每次命中的就是「該次股利事件對應的明細」— 不會落到重複的 last_dividend,也不會浪費 API 打沒事件的日期。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `dividend_event_list.stk_code` |
| `dividend` | JSONB | `/dividend?as_of=X` endpoint 整包 JSON（含 `dividend.{year, reference_date, cash_dividend, stock_dividend, cash_ex_dividend_date, cash_payment_date, stock_ex_dividend_date, announcement_date}`） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `reference_date`） | `dividend_event_list.reference_date` |

---

## dividend

- **上游 SQL**：[raw_dividend](#raw_dividend)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位 align `DividendSection`;主鍵顯示規則與 [financial_quarterly](#financial_quarterly) 同 (`stk_code` + `as_of`)。每列 = 該公司歷史某次除息事件對應的股利明細。
- 設計理念（rule 15）：每列對應歷史一次真實股利事件（by `reference_date`）,非規則性時間格點採樣。
- 設計理念（rule 13）：不做 WHERE 過濾,保留 raw 母體所有 rows。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend.stk_code` |
| `as_of` | DATE | 本筆股利事件的除息日（seed 日期，I004） | `raw_dividend.as_of` |
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

- **上游 SQL**：[dividend_event_yfinance_list](#dividend_event_yfinance_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/company/{stock_id}/dividend/yfinance?as_of={reference_date}`
  - 上游：yfinance Python Library — `Ticker.dividends`
- 與 `raw_dividend` (FinMind 版) 結構完全一致,純資料源替代版。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `dividend_event_yfinance_list.stk_code` |
| `dividend` | JSONB | `/dividend/yfinance?as_of=X` endpoint 整包 JSON（結構與 raw_dividend 一致） | `custom.http_get_content(url)` |
| `as_of` | DATE | 本筆對應的查詢基準日（即 `reference_date`） | `dividend_event_yfinance_list.reference_date` |

---

## dividend_yfinance

- **上游 SQL**：[raw_dividend_yfinance](#raw_dividend_yfinance)
- **HTTP API endpoint**：無（純 JSON 攤平）
- 欄位完全與 [dividend](#dividend) align,只差在資料源（yfinance vs FinMind）。
- `cash_dividend`：Yahoo 的 `Ticker.dividends` 會依之後的配股（記為 split）往回調低；endpoint 已用 `Ticker.splits` 還原成當年實際配發金額（I003）。沒有 split 紀錄的其他還原調整（例：2882 2021–2022，比值 1.0157）無法反推，**實際配發金額以 [dividend](#dividend)（FinMind）為準**。`stock_dividend` / `cash_payment_date` / `stock_ex_dividend_date` / `announcement_date` 上游 endpoint 已回 null/0,此處保留同 schema。
- 設計理念（rule 15）：每列對應歷史一次真實股利事件（by `reference_date`）。
- 設計理念（rule 13）：不做 WHERE 過濾,保留 raw 母體所有 rows。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `raw_dividend_yfinance.stk_code` |
| `as_of` | DATE | 本筆股利事件的除息日（seed 日期，I004） | `raw_dividend_yfinance.as_of` |
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
- 設計理念（rule 13 例外）：只以 `WHERE value IS NOT NULL` 濾掉陣列內的 null 季度（取代舊版 `quarter <> '[null]'`，舊版擋不住 `[null, "2025-06-30"]` 這類混合陣列）。
- 設計理念（rule 6）：eps 與 net_income 的季度清單在同一列 raw 內大量重疊（實測 16 列中 4 列重複），故在 `LATERAL` 子查詢內**逐列** `DISTINCT`；不在外層 DISTINCT，因為外層 DISTINCT 會讓 poc 的 `LIMIT 1` 無法提早結束。殘餘限制：同公司不同年度列帶回相同季度的跨列重複（目前資料未出現）無法在不阻斷串流下消除，由 seed insert 的 `EXCEPT` 去重。

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
| `as_of` | DATE | 本筆指標的查詢基準日（seed 日期，I004） | `raw_quarterly_financials_yfinance.as_of` |
| `stock_id` | TEXT | 股票代號（API 規範格式） | `financials.stock_id` |
| `eps_ttm` | NUMERIC | EPS TTM | `financials.eps.ttm` |
| `latest_quarter_date` | DATE | 最新可得的單季財報日 | `financials.eps.latest_quarter_date` |
| `latest_quarter_eps` | NUMERIC | 最新單季的 EPS | `financials.eps.latest_quarter_value` |
| `net_income_ttm` | NUMERIC | 稅後淨利 TTM | `financials.net_income.ttm` |
| `latest_quarter_net_income` | NUMERIC | 最新單季稅後淨利 | `financials.net_income.latest_quarter_value` |
| `operating_margin_pct` | NUMERIC | 營業利潤率 (%) | `financials.operating_margin_pct` |
| `revenue_ttm` | NUMERIC | 營收 TTM（取自財報） | `financials.revenue_ttm_from_financial_statements` |

---

## ohlcv_monthly_list

- **上游 SQL**：[company_basic_info_list](#company_basic_info_list)
- **HTTP API endpoint**：無（純 SQL `generate_series`）
- **角色**：seed（`_list`）— 每 `(stk_code, month_start_date)` 一列，作為 `/api/ohlcv` 事件母體。上市 + 上櫃 **統一在同一張 seed**，不再分流（v3 曾經拆為 `ohlcv_daily_twse_list` / `ohlcv_daily_tpex_list` 兩張，v4 已合併）。
- **設計理念（rule 15 · 母體大小）**：從 `GREATEST(listing_date 所屬月, 2010-02-01)` 起，月粒度 `generate_series` 到 `CURRENT_DATE`。~1900 檔（上市 + 上櫃）× 平均 12 年 × 12 月 ≈ 270k 列 seed 上限。
- **設計理念（rule 20 · 資料源分流）**：seed 層**不做**分流。backend `/api/ohlcv` 統一 endpoint 內部依 `company_basic_info_list.market` 分發到 TWSE STOCK_DAY 或 TPEx tradingStock 上游，分流已下沉到 backend 層。單一 `batch_size.json` → `ohlcv_monthly_list` 條目控制 refresh 節奏。
- **設計理念（rule 6 · rows 唯一）**：`(stk_code, month_start_date)` 天然唯一 — `generate_series` step `INTERVAL '1 month'` 且起點為月初。
- **設計理念（rule 13）**：`listing_date IS NOT NULL` 為技術性 guard（`GREATEST` 會忽略 NULL），非業務過濾。
- **起點 2010-02-01（commit 614005c）**：backend 對上市股 `from < 2010-01-04`（STOCK_DAY hard block）會改走 MI_INDEX 逐日全市場 payload（每月 ~20 次呼叫）；起點設在 2010-02-01 讓所有月份都走 STOCK_DAY 一月一次。代價是 2004-02 ~ 2010-01 不在母體內；要補齊時把下限改回 `2004-02-01`（backend 已支援）。
- **每日新月觸發機制**：每月 1 日 `CURRENT_DATE` 推進到新月 → `generate_series` 上界改變 → 每檔股票多一列新的 `month_start_date` → pipeline `EXCEPT` anti-join 判定為新列並 INSERT → 觸發 raw 拉該股該月最新日 K。
- **傳遞欄位**：SELECT 出 `listing_date`，向下游 `raw_ohlcv_monthly` 傳遞（未來若需以 listing_date 為條件過濾早於上市日的空月，可在下游使用）。

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `company_basic_info_list.stk_code` |
| `month_start_date` | DATE | 該月第一日（西元） | `generate_series(GREATEST(month_of(listing_date), '2010-02-01'), CURRENT_DATE, INTERVAL '1 month')` |
| `listing_date` | DATE | 上市/上櫃日 | `company_basic_info_list.listing_date` |

---

## raw_ohlcv_monthly

- **上游 SQL**：[ohlcv_monthly_list](#ohlcv_monthly_list)
- **HTTP API endpoint**：`GET http://host.docker.internal:5002/api/ohlcv?stk_code={stk_code}&from={month_start}&to={month_end}`
  - 上游 endpoint：twstock_api 統一 `/api/ohlcv`（於 `app/main.py` 定義）— backend 依 `company_basic_info_list.market` 判別後分發到 [TWSE 證交所 · 個股日成交資訊 STOCK_DAY](https://www.twse.com.tw/zh/trading/historical/stock-day.html) 或 [TPEx 櫃買中心 · 個股日成交資訊 tradingStock](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/mi-pricing.html)（per-stock-per-month）。
  - Backend 策略：`range_days > 7` 走 `per_stock_month`，一次呼叫上游拿整月日 K（~20 個交易日）；`range_days ≤ 7` 走 `per_day_market`，走 MI_INDEX / dailyQuotes 全市場 payload + 磁碟 cache（seed 打的月範圍固定 ~28~31 天，實際永遠走 `per_stock_month`）。
  - 為何走 `/api/ohlcv` 而非直接打 TWSE / TPEx：backend 已把 TPEx「張 / 仟元」單位對齊「股 / 元」、把民國年轉西元、把「漲跌方向 + 漲跌價差」合成 signed `change`；raw 層與 normalized 層 SQL 大幅簡化，且 TWSE / TPEx 兩市場共享同一 endpoint（未來若上游 URL 變動只需改 backend）。
- **設計理念（rule 9 · one payload per event）**：一個 `(stk_code, month_start_date)` = 一列 payload；範圍為「該月 1 號 → 當月月底」（`custom.month_end(month_start_date)`）。
- **設計理念（rule 1 / 3）**：URL 組合僅使用 IMMUTABLE building blocks（月底用 `db/setting.sql` 的 `custom.month_end`，不用 `::DATE` cast）；HTTP 呼叫走 `custom.http_get_content`。
- **payload 空回情境**：`found=false`（stk_code 不在 `company_basic_info_list`）或該月無交易日 → `rows=[]`，正規化層 `CROSS JOIN LATERAL` 自然攤平 0 列。
- **payload 結構（供下游正規化參考）**：backend 已把單位對齊到「股 / 元」，欄位命名與市場無關：
  ```
  { found: true, stk_code: '5483', market: '上櫃',
    from_date: '2024-01-01', to_date: '2024-01-31',
    strategy: 'per_stock_month',
    rows: [{ trade_date: '2024-01-02', stk_code: '5483',
             open: 196.0, high: 201.0, low: 193.5, close: 201.0,
             volume: 6832000.0, trade_value: 1344589000.0,   -- TPEx 已由 backend ×1000
             transaction_count: 3436.0, change: 5.0 }, ...],
    source: 'TPEx tradingStock (per-stock-per-month)' }
  ```

| 欄位 | 型別 | 中文描述 | 來源 |
| --- | --- | --- | --- |
| `stk_code` | TEXT | 股票代號 | `ohlcv_monthly_list.stk_code` |
| `month_start_date` | DATE | 該月第一日 | `ohlcv_monthly_list.month_start_date` |
| `ohlcv` | JSONB | 該股該月日 K JSON | `custom.http_get_content('/api/ohlcv?stk_code=X&from=Y&to=Z')` |
| `listing_date` | DATE | 上市/上櫃日（透傳） | `ohlcv_monthly_list.listing_date` |

---

## ohlcv_daily

- **上游 SQL**：[raw_ohlcv_monthly](#raw_ohlcv_monthly)
- **HTTP API endpoint**：無（純 JSONB 攤平）
- **設計理念（rule 16 · pg_ivm 相容）**：只用 `CROSS JOIN LATERAL custom.jsonb_array_elements(ohlcv->'rows')` 攤平陣列成逐日一列。使用 `custom.jsonb_array_elements`（IMMUTABLE + PARALLEL SAFE wrapper，定義於 `db/setting.sql`）而非原生 `jsonb_array_elements`（STABLE + non-PARALLEL SAFE），以維持 pg_ivm 增量物化相容與 parallel query 可用性。
- **設計理念（rule 12 · 民國年 → 西元）**：`/api/ohlcv` 已由 backend 統一轉為西元 ISO `'YYYY-MM-DD'`；normalized 層直接 `custom.parse_iso_date(item->>'trade_date')`，不再需要 `make_date + SUBSTRING` 展開民國年。
- **設計理念（rule 13）**：不做過濾；`found=false` 或 `rows=[]` 天然攤平 0 列。
- **NULL-safe**：`COALESCE(ohlcv->'rows', '[]'::jsonb)` 防 payload 缺 `rows` key。
- **上市 + 上櫃 UNION**：v4 之後 TWSE / TPEx 已合併同一張 view — 沒有 `market='sii'|'otc'` 分流，也不需要下游 UNION 兩個 view。判定市場請 JOIN `company_basic_info_list.market`。
- **數字欄位處理**：backend 已把千分位逗號剝除、把 signed `+`/`-` 前綴合入 `change`、把 TPEx 單位對齊「股 / 元」，SQL 只需 `::NUMERIC` CAST。

| 欄位 | 型別 | 中文描述 | 來源 JSON 路徑 |
| --- | --- | --- | --- |
| `trade_date` | DATE | 交易日期（西元） | `rows[*].trade_date` |
| `stk_code` | TEXT | 股票代號 | `rows[*].stk_code` |
| `open` | NUMERIC | 開盤價 | `rows[*].open` |
| `high` | NUMERIC | 最高價 | `rows[*].high` |
| `low` | NUMERIC | 最低價 | `rows[*].low` |
| `close` | NUMERIC | 收盤價 | `rows[*].close` |
| `volume` | NUMERIC | 成交股數 | `rows[*].volume` |
| `trade_value` | NUMERIC | 成交金額（元） | `rows[*].trade_value` |
| `transaction_count` | NUMERIC | 成交筆數 | `rows[*].transaction_count` |
| `change` | NUMERIC | 漲跌（signed） | `rows[*].change` |
