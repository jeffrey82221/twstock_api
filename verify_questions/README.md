# verify_questions — 以投資問題驅動的資料品質驗證

用「真的會拿來做投資決策的問題」當作資料庫的端到端測試。每一題都走同一條路：

1. 用 `crawl_insert_filtered` 補爬這題需要的資料。
2. 用 `select` 查出決策資料。
3. 用 SQL 斷言驗證資料品質。
4. 斷言失敗就追到根因，修 [`db/poc/*.sql`](../db/poc/) 或 [`app/`](../app/) 的 API。
5. 清掉壞資料、重爬，再驗證一次。

問題、執行紀錄、issue 都以檔案留在本資料夾。日常操作是把 [§11 AI 指令](#11-ai-指令) 貼給 Claude，讓它照本文件推進。

---

## 1. 核心概念

[`tests/`](../tests/) 驗證「程式照規格運作」；本系統驗證「資料拿來做決策時是否可信」。

| | `tests/` | `verify_questions/` |
| --- | --- | --- |
| 測試案例來源 | 函式／endpoint 規格 | 投資決策問題 |
| 資料 | fixture、mock | live DB 的 `pop` schema（真實爬到的資料） |
| 擅長抓的錯 | parser、route、SQL 語法 | 單位錯、日期錯位、覆蓋缺口、跨來源矛盾、白爬的列 |
| 通過條件 | assert | SQL 斷言回傳 0 列違規 |

一題問題通常橫跨多張 `pop` 表與多個上游，最容易暴露單元測試看不到的整合問題。

四個原則：

- **斷言就是 SQL**：每個斷言是一句可以直接丟給 MCP `select` 的 `SELECT`，回傳「違規列」，0 列＝通過。
- **補爬是按需的**：只爬問題需要的列，不等 cron 輪到，也不浪費 API 額度。
- **修復要閉環**：程式改好不算完。seed 列一旦 insert，IMMV 結果就固定了，所以還要清 cache、刪壞列、重爬，再重跑原本失敗的斷言，issue 才能關。
- **不為了通過而放寬斷言**：斷言只有在能說明「現實就是如此」時才可以改（例：春節休市），而且要寫進問題卡的「已知限制」。

```
            ┌───────────────── 指令 1 盤點 ─────────────────┐
            ▼                                               │
   snapshots/ ＋ LEDGER 覆蓋矩陣                              │
            │                                               │
      指令 2 出題 ─► questions/Q###.md (draft)               │
            │                                               │
      指令 3 執行 ─► 補爬 ─► 取數 ─► 斷言 ─► runs/<日期>_Q###.md │
            │                          │                    │
            │ 全部通過 → passing        │ 失敗                │
            │                          ▼                    │
            │              issues/I###.md (open)            │
            │                          │                    │
            │              指令 4 診斷 → 根因層級 L0–L7       │
            │                          │                    │
            │              指令 5 修復 → 測試 → 上線 → 重爬 → 重跑斷言
            │                          │                    │
            └──── 指令 6 回歸（定期重跑所有 passing 題）◄──────┘
```

## 2. 前置條件

與頂層 [`README.md`](../README.md) 相同：

- (3) Postgres 正在執行
- (5) API 在 port 5002
- (6) MCP `twstock-db` 已連線
- (8) `pop` schema 已建立

開工前快速確認：

```bash
claude mcp get twstock-db                 # Status: ✔ Connected
curl -s http://localhost:5002/api/health  # API 存活
```

## 3. 資料夾結構

```
verify_questions/
├── README.md               # 本文件：方法與 AI 指令
├── LEDGER.md               # 總帳：問題狀態、issue 清單、覆蓋矩陣、品質趨勢
├── questions/              # 問題卡，一題一檔
│   └── Q001-valuation-per-percentile.md
├── runs/                   # 每次執行一份：補爬紀錄、斷言結果、決策資料、結論
│   └── 2026-11-24_Q001.md
├── issues/                 # 資料品質問題，一個根因一檔
│   └── I001-sbl-history-pre2015-404.md
├── checks/                 # 可重用的斷言模板（Jinja 參數，同 db/poc 的 {{ schema }} 慣例）
│   └── ohlcv_daily/ohlc_consistency.sql
└── snapshots/              # 每次盤點的 MCP 原始輸出
    └── 2026-11-24.json
```

- 問題用 `Q###`、issue 用 `I###`，三位數遞增、不重用；檔名的 slug 用英文小寫。
- `checks/` 的模板用 `{{ stk_codes }}`、`{{ since }}` 參數。問題卡裡放的是代入參數後、可以直接執行的 SQL。

## 4. 系統循環

| 步驟 | 做什麼 | 用到的 MCP tool | 產出 |
| --- | --- | --- | --- |
| 1 盤點 | 拍下資料庫現況，和上一份比較 | `crawl_pop_tables`、`pop_raw_quality`、`crawl_status(1440)`、`http_stats(1440)` | `snapshots/<日期>.json`、LEDGER 品質趨勢與覆蓋矩陣 |
| 2 出題 | 依覆蓋缺口與策略維度（§5）寫問題卡 | `describe_table`、`select`（確認欄位與現有資料） | `questions/Q###.md`（`status: draft`） |
| 3 補爬 | 依問題卡的 `crawl` 區塊補資料（規則見 §8） | `select` 看現有 → `crawl_insert_filtered` dry run → 實際寫入 | run 紀錄的「補爬」表 |
| 4 取數 | 執行問題卡的決策 SQL | `select` | run 紀錄的「決策資料」與結論 |
| 5 驗證 | 逐一執行斷言，判定 PASS / FAIL / INCONCLUSIVE | `select` | run 紀錄的「斷言結果」；失敗依 §7.3 分流 |
| 6 診斷修復 | 根因分析（§9），修程式並閉環（§10） | `select`（含 `job_control.*`、`hidden.*`）、`poc_rebuild_views`、`pop_check_definitions`、`pop_update_mat_views` | `issues/I###.md` 更新、程式 diff、測試結果 |
| 7 回歸 | 定期只跑斷言、不補爬，找出退化 | `select` | LEDGER 狀態更新；退化就開 issue |

**補爬前後的差異**：補爬前的時間序列缺口是「還沒爬」，不算品質問題。完整性斷言一律在步驟 3 之後判定；補爬後仍然有洞，才是品質問題（seed 沒產生該格點，或爬取失敗）。

## 5. 問題設計

### 5.1 策略維度 × 資料表

出題時優先挑「資料錯了會直接導致錯誤決策」的問題，並刻意安排跨來源比對，因為最多品質問題藏在那裡。

| 策略維度 | 典型決策問題 | 主要 `pop` 表 | 跨來源／內部一致性比對機會 |
| --- | --- | --- | --- |
| 估值 | PER／PBR 在自身 N 年第幾百分位？低於大盤？ | `company_valuation`、`market_valuation_summary` | `close_price` vs `ohlcv_daily.close`；`close_price / eps_ttm ≈ per`；`close_price / bvps ≈ pbr`；`eps_ttm` vs `financial_quarterly_yfinance.eps_ttm` |
| 成長 | 月營收 YoY 連 3 個月 > 20%？季毛利率連升？ | `monthly_revenue_twse`、`monthly_revenue`、`mops_quarterly_financials`、`financial_quarterly_yfinance` | TWSE／MOPS vs FinMind 月營收；MOPS vs yfinance 季 EPS；季營收量級 vs 三個月月營收合計 |
| 體質 | ROE > 15% 且負債比 < 50% 連 3 年？ | `financial_analysis`、`financial_yearly_yfinance` | `financial_analysis.eps` vs MOPS 四季 EPS 合計 |
| 股利 | 連續配息幾年？現金殖利率？除息後多久填息？ | `dividend`、`dividend_yfinance`、`company_valuation`、`ohlcv_daily` | FinMind vs yfinance 現金股利；`dps / close_price` vs `dividend_yield_pct` |
| 籌碼 | 外資連續買超？外資持股比上升？借券賣出增加？ | `institutional_net_buy_sell`、`foreign_ownership`、`sbl_history` | 法人買賣超方向 vs 外資持股比變化方向 |
| 技術 | 站上 60 日均線？近 20 日波動度？ | `ohlcv_daily` | 日 K vs 估值收盤價；交易日曆 vs 法人資料的交易日 |
| 產業 | 同產業鏈誰估值最低、成長最快？產品營收結構？ | `chain_info_list`、`company_basic_info_list`、`company_business_items`、`product_revenue` | `product_revenue.total_revenue` vs 月營收 |
| 事件 | 重大新聞後股價反應？ | `finmind_news`、`ohlcv_daily` | 新聞日 vs 交易日對齊 |
| 市場 | 大盤 PER 是否在歷史高檔？ | `market_valuation_summary` | 市場市值 vs 個股 `market_cap` 加總量級 |

### 5.2 一題好問題的條件

- **可決策**：有明確的 if-then（「PER 低於第 20 百分位就列入觀察」），不是單純「列出資料」。
- **範圍可負擔**：補爬量要在 §8.3 預算內。通常 ≤ 5 檔、時間窗 ≤ 3 年。
- **涵蓋邊界**：每批問題合計至少涵蓋上市與上櫃、金融股、近一年新上市、除權息月份、長假（春節）。
- **至少一個跨來源或內部一致性斷言**：單一來源的資料自己跟自己比，只能抓到格式錯，抓不到值錯。
- **斷言數 3–8 個**：每個品質維度（§7.1）最多一個，避免重複。

### 5.3 代表性股票池

| 代號 | 名稱 | 用途 | 2026-11-24 狀態 |
| --- | --- | --- | --- |
| 2330 | 台積電 | 上市大型權值，資料最完整，當作基準 | 已在 `company_basic_info_list` |
| 2317 | 鴻海 | 上市大型 | 已在 |
| 2454 | 聯發科 | 上市大型 | **尚未進 `company_list`**，需先走 §8.1 依賴鏈 |
| 1101 | 台泥 | 傳產、穩定配息 | **尚未進 `company_list`** |
| 2882 | 國泰金 | 金融股：財報科目不同，毛利率等常為 NULL | 已在 |
| 6488 | 環球晶 | 上櫃（TPEx 路徑在 `ohlcv` 只有 17／1093 列，覆蓋低） | 已在 |
| 7769 | 鴻勁 | 2025-11-27 新上市：資料起點邊界 | 已在 |

## 6. 檔案格式

### 6.1 問題卡 `questions/Q###-<slug>.md`

以下範例的 SQL 已於 2026-11-24 在 live DB 驗證可執行。

````markdown
---
id: Q001
title: 2330、2317 目前本益比在自身近 3 年的第幾百分位？
strategy: valuation          # §5.1 的維度
status: draft                # draft | passing | failing | blocked
stocks: ["2330", "2317"]
window: {since: 2023-11-01, until: 2026-11-24}
pop_tables: [company_valuation, ohlcv_daily]
crawl:                       # 步驟 3 依序執行；budget = 本題此 seed 最多補幾列
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["2330", "2317"]}
    order_by: ["valuation_date"]
    budget: 72               # 3 年 × 12 月 × 2 檔
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["2330", "2317"]}
    order_by: ["month_start_date"]
    budget: 24
issues: []
last_run: null
---

## 決策情境
目前 PER 低於自身 3 年第 20 百分位、且殖利率高於 3 年中位數 → 列入分批買進觀察；
PER 高於第 80 百分位 → 不追價。

## 決策 SQL
```sql
WITH v AS (
  SELECT stk_code, as_of, per, dividend_yield_pct
  FROM pop.company_valuation
  WHERE stk_code IN ('2330','2317') AND as_of >= DATE '2023-11-01'
), latest AS (
  SELECT DISTINCT ON (stk_code) stk_code, as_of, per, dividend_yield_pct
  FROM v ORDER BY stk_code, as_of DESC
)
SELECT l.stk_code, l.as_of, l.per,
       round(100.0 * (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code AND v.per <= l.per)
             / NULLIF((SELECT count(per) FROM v WHERE v.stk_code = l.stk_code), 0), 1) AS per_pctile,
       l.dividend_yield_pct,
       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY dividend_yield_pct)
          FROM v WHERE v.stk_code = l.stk_code) AS yield_median,
       (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code) AS n_months
FROM latest l
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每檔每月 5 日都有一筆估值
```sql
WITH expected AS (
  SELECT s.stk_code, (date_trunc('month', g) + INTERVAL '4 days')::date AS as_of
  FROM unnest(ARRAY['2330','2317']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2023-11-01', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of AS missing_as_of
FROM expected e
LEFT JOIN pop.company_valuation v ON v.stk_code = e.stk_code AND v.as_of = e.as_of
WHERE v.stk_code IS NULL AND e.as_of <= CURRENT_DATE
```

### C2 internal — PER／PBR 可由收盤價、EPS、BVPS 互推（誤差 5%），數值在合理範圍
```sql
SELECT stk_code, as_of, per, pbr, dividend_yield_pct, close_price, eps_ttm, bvps
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317') AND as_of >= DATE '2023-11-01'
  AND (per <= 0 OR per > 300 OR pbr <= 0 OR pbr > 50
       OR dividend_yield_pct < 0 OR dividend_yield_pct > 25
       OR abs(close_price / NULLIF(eps_ttm, 0) - per) > 0.05 * per
       OR abs(close_price / NULLIF(bvps, 0) - pbr) > 0.05 * pbr)
```

### C3 cross-source — 估值收盤價 = 日 K 收盤價（誤差 1%）
```sql
SELECT v.stk_code, v.data_date, v.close_price, o.close
FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('2330','2317') AND abs(v.close_price - o.close) > 0.01 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('2330','2317')
```

### C4 timeliness — 實際資料日與取樣日差距 ≤ 12 天
```sql
SELECT stk_code, as_of, data_date, as_of - data_date AS lag_days
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317') AND data_date < as_of - 12
```

## 已知限制
- 虧損期間 PER 為 NULL，這是定義上的空值，不是缺值；百分位只算非 NULL 月份。
- C4 容忍 12 天：2022-02-05 取樣日的實際資料日是 2022-01-26（春節休市 10 天）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
````

### 6.2 執行紀錄 `runs/<YYYY-MM-DD>_Q###.md`

````markdown
# Q001 run — 2026-11-24

## 補爬
| seed | filters | dry-run pending | inserted | 秒數 | 本次列的 raw 可用率 |
| --- | --- | ---: | ---: | ---: | ---: |

## 決策資料
（決策 SQL 的結果表）

## 斷言結果
| 斷言 | 結果 | 違規列數 | 可比對列數 | 說明 |
| --- | --- | ---: | ---: | --- |
| C1 | PASS | 0 | — | |

## 結論
- 投資面：（依決策情境給出結論；資料有 FAIL 時明寫「結論不可信」）
- 品質面：新開 issue／附加到既有 issue／修改斷言（附理由）
````

### 6.3 Issue `issues/I###-<slug>.md`

````markdown
---
id: I001
title: raw_sbl_history 在 2015-09 以前全部 404
severity: S4                 # §7.4
layer: null                  # 診斷後填 L0–L7（§9.2）
status: open                 # open | diagnosed | fixing | verifying | closed | wontfix
pop_tables: [raw_sbl_history, sbl_history]
seeds: [sbl_history_monthly_list]
found_by: [Q005]
---

## 症狀（附可重現的 SQL）
## 證據（http_log、直接呼叫 API 的結果、上游官網對照）
## 根因
## 修復方案（改哪些檔、要不要新增 _v2 SQL、影響多少既有列、預估重打 API 次數）
## 修復紀錄（diff 摘要、測試結果）
## 重爬與驗證（http_cache_evict／crawl_delete_rows／crawl_insert_filtered 的實際數字、重跑的斷言結果）
````

### 6.4 `LEDGER.md`

```markdown
# Ledger

## 問題
| ID | 策略 | 標題 | 狀態 | 最後執行 | 未結 issue |

## Issues
| ID | 嚴重度 | 層級 | 標題 | 狀態 | 發現於 | 修復 commit |

## 覆蓋矩陣（pop 表 × 問題）
| pop 表 | 覆蓋的問題 | 最近斷言結果 |
（列出所有 pop 正規化表；沒有任何問題覆蓋的表就是下一輪出題的優先目標）

## 品質趨勢（每次盤點一行）
| 日期 | raw 平均 usable% | 最低三個 raw（usable%） | 新開／關閉 issue |
```

`snapshots/<日期>.json` 的格式是 `{"taken_at": ..., "crawl_pop_tables": [...], "pop_raw_quality": [...], "crawl_status": [...], "http_stats": {...}}`，內容是各 tool 的原始輸出。

## 7. 品質斷言

### 7.1 品質維度

| 維度 | 問的是 | 範例（回傳違規列） |
| --- | --- | --- |
| 可得性 availability | 補爬的列，raw 是否可用 | `SELECT stk_code, as_of FROM pop.raw_company_valuation WHERE stk_code IN (...) AND (company_valuation IS NULL OR company_valuation->>'found' = 'false')` |
| 完整性 completeness | 期望的每個格點都有列 | `generate_series` 產生期望格點 `LEFT JOIN` 實際表，取 NULL（見 Q001 C1） |
| 唯一性 uniqueness | 主鍵無重複 | `SELECT stk_code, trade_date FROM pop.ohlcv_daily GROUP BY 1,2 HAVING count(*) > 1` |
| 合理性 validity | 數值範圍、欄位間的物理關係 | `WHERE high < GREATEST(open, close) OR low > LEAST(open, close) OR close <= 0 OR volume < 0` |
| 內部一致 internal | 同表欄位可互推 | `abs(close_price / eps_ttm - per) > 0.05 * per`（Q001 C2） |
| 跨來源一致 cross-source | 不同來源描述同一事實 | FinMind vs yfinance 現金股利：`pop.dividend JOIN pop.dividend_yfinance USING (stk_code, reference_date)` 比 `cash_dividend` |
| 時效 timeliness | 資料日與取樣日的落差 | `WHERE data_date < as_of - 12` |
| 單位／日期 unit | 民國年、千元／百萬、張／股、% vs 小數 | 跨表量級比對（季營收 vs 三個月月營收合計），比值偏離 1 一個數量級以上就算違規 |

### 7.2 寫法規範

- 單一 `SELECT` 或 `WITH`，不可含 `;`（MCP `select` 的限制）。可以讀 `pop`、`hidden`、`job_control`。
- 回傳的欄位要足以直接診斷：主鍵、日期，以及出問題的值本身，不要只回 `count(*)`。
- 跨來源斷言要另附「可比對列數」查詢。可比對列數為 0 時，結果是 **INCONCLUSIVE**，不是 PASS。
- 斷言只讀 `pop`，不讀 `poc`。`poc` view 每次查詢都會打 HTTP，結果不穩定，也會消耗額度。
- 門檻要寫出理由（例如 C4 的 12 天＝春節最長休市），放在斷言標題或「已知限制」。

### 7.3 斷言失敗後的分流

1. **排除暫時性**：同一斷言重跑一次。若是爬取當下上游 5xx 或逾時，重跑就會通過；記在 run 的說明欄，不開 issue。
2. **判斷是否現實如此（L7）**：休市、虧損 PER 為 NULL、金融股沒有毛利率、新上市資料不足。這類情況修改斷言或縮小範圍，並寫進「已知限制」，不開 issue。
3. **其餘開 issue**：先搜尋 `issues/`，看有沒有相同 `pop_tables` 與症狀的未結 issue。有就把本題加進該 issue 的 `found_by`，沒有才開新的。問題卡的 `status` 改為 `failing`，`issues` 欄加上 issue 編號。
4. **資料拿不到（L0 或額度）**：`status: blocked`，寫明阻擋原因與解除條件（例：「等 FinMind token」）。

### 7.4 嚴重度（修復順序 S1 → S4）

| 等級 | 定義 | 為什麼排這個順序 |
| --- | --- | --- |
| S1 錯值 | 有值但錯（單位、日期錯位、張冠李戴） | 看起來正常，會直接誤導決策，最危險 |
| S2 缺值 | NULL、found=false、時間序列有洞 | 看得出來，但會讓結論偏誤（例：百分位少算月份） |
| S3 過時 | 資料日落後太多、未更新 | 影響時效型決策 |
| S4 浪費 | 必定作廢的 API 呼叫、重複請求 | 不影響正確性，但拖慢所有 seed 的填滿速度 |

## 8. 補爬規則（`crawl_insert_filtered`）

### 8.1 seed 依賴鏈

主題 seed 的母體來自上游 seed。股票還沒進上游時，主題 seed 的 dry run 會是 `pending_matching = 0`，必須由上往下補。

```
chain_list → chain_info_list → company_list ─┬─► company_basic_info_list ─┬─► ohlcv_monthly_list
                                             │   （raw_company_info 是    ├─► company_valuation_monthly_list
                                             │     found=true 的公司才進） ├─► institutional_monthly_list
                                             │                           ├─► foreign_ownership_monthly_list
                                             │                           ├─► sbl_history_monthly_list
                                             │                           ├─► financial_month_twse_list / financial_month_list
                                             │                           ├─► mops_quarterly_financials_quarter_list
                                             │                           ├─► financial_analysis_year_list
                                             │                           ├─► financial_year_list
                                             │                           ├─► finmind_news_monthly_list
                                             │                           └─► financial_year_yfinance_list ─► financial_quarter_yfinance_list
                                             ├─► company_finmind_list ─► (raw_dividend_history) ─► dividend_event_list
                                             └─► (raw_dividend_history_yfinance) ─► dividend_event_yfinance_list
product_revenue_filer_scope_list ─► (raw_product_revenue_filers) ─► product_revenue_filer_list
market_valuation_date_list（市場層級，無個股上游）
```

例：要分析 2454，先做 `company_list {stk_code: "2454"}`，再做 `company_basic_info_list {stk_code: "2454"}`，最後才做主題 seed。

seed 的 INSERT 要求**所有欄位 NOT NULL**（[`pipeline.py` `_build_seed_insert_sql`](../pipeline.py)）。只要 `hidden.<seed>` 的任一欄位為 NULL，該列永遠不會被選進 `pop`，下游也就不會有這檔股票。

### 8.2 seed 速查表

| seed | 常用 filter 欄位 | 時間欄（`order_by`） | 下游 `pop` 表 | 上游資料源 |
| --- | --- | --- | --- | --- |
| `company_list` | `stk_code` | — | `raw_company_info`、`company_business_items`、`raw_dividend_history_yfinance` | TWSE/TPEx OpenAPI、經濟部、yfinance |
| `company_basic_info_list` | `stk_code`、`market`、`industry_code` | `listing_date` | （主題 seed 的母體） | 純 SQL |
| `company_finmind_list` | `stk_code` | — | `raw_dividend_history` | FinMind |
| `ohlcv_monthly_list` | `stk_code` | `month_start_date` | `raw_ohlcv_monthly`、`ohlcv_daily` | TWSE STOCK_DAY／TPEx |
| `company_valuation_monthly_list` | `stk_code` | `valuation_date` | `raw_company_valuation`、`company_valuation` | TWSE BWIBBU_d |
| `market_valuation_date_list` | `valuation_date` | `valuation_date` | `raw_market_valuation_summary`、`market_valuation_summary` | TWSE BWIBBU_d |
| `institutional_monthly_list` | `stk_code` | `month_start_date` | `raw_institutional_net_buy_sell`、`institutional_net_buy_sell` | TWSE T86／TPEx |
| `foreign_ownership_monthly_list` | `stk_code` | `month_start_date` | `raw_foreign_ownership`、`foreign_ownership` | TWSE MI_QFIIS |
| `sbl_history_monthly_list` | `stk_code` | `month_start_date` | `raw_sbl_history`、`sbl_history` | TWSE t13sa870 |
| `financial_month_twse_list` | `stk_code` | `month_start_date` | `raw_monthly_revenue_twse`、`monthly_revenue_twse` | TWSE OpenAPI＋MOPS t21sc03 |
| `financial_month_list` | `stk_code` | `month_start_date` | `raw_monthly_revenue`、`monthly_revenue` | FinMind |
| `mops_quarterly_financials_quarter_list` | `stk_code` | `quarter_end` | `raw_mops_quarterly_financials`、`mops_quarterly_financials` | MOPS t163sb06／04 |
| `financial_analysis_year_list` | `stk_code` | `year` | `raw_financial_analysis`、`financial_analysis` | MOPS t51sb02 |
| `financial_year_list` | `stk_code` | `year_start_date` | `raw_yearly_financials`（無正規化表） | FinMind |
| `financial_year_yfinance_list` | `stk_code` | `year_start_date` | `raw_yearly_financials_yfinance`、`financial_yearly_yfinance` | yfinance |
| `financial_quarter_yfinance_list` | `stk_code` | `quater`（schema 拼法如此） | `raw_quarterly_financials_yfinance`、`financial_quarterly_yfinance` | yfinance |
| `dividend_event_list` | `stk_code` | `reference_date` | `raw_dividend`、`dividend` | FinMind |
| `dividend_event_yfinance_list` | `stk_code` | `reference_date` | `raw_dividend_yfinance`、`dividend_yfinance` | yfinance |
| `finmind_news_monthly_list` | `stk_code` | `month_start_date` | `raw_finmind_news`、`finmind_news` | FinMind |
| `product_revenue_filer_list` | `stk_code`、`ym` | `report_month` | `raw_product_revenue`、`product_revenue` | MOPS t05st08 |

### 8.3 預算與護欄

每次執行一題時，各上游的補爬上限如下。這些是初始值：觀察 `http_stats` 與 `crawl_runs` 後，直接修改這張表。

| 上游 | 每題上限（seed 列） | 單次 `row_cnt` | 理由 |
| --- | ---: | ---: | --- |
| FinMind | 20 | ≤ 5 | 免費層 300 req/hr，cron 已用約 240。超額回 402，該列被**永久凍結**成 found=false |
| TWSE www | 120 | ≤ 12 | 非官方限制約每 5 秒 3 次，過頻會暫時封 IP |
| MOPS | 60 | ≤ 8 | 過頻回「查詢過於頻繁」 |
| yfinance | 150 | ≤ 20 | 寬鬆 |
| `company_list`／`company_basic_info_list` | 50 | ≤ 20 | `company_list` 每列會打經濟部與 yfinance |

規則：

1. **先看現有**：先用 `select` 查 `pop` 已有的列，能不爬就不爬。
2. **dry run 再寫入**：看 `pending_matching` 和產生的 SQL，確認在預算內才以 `dry_run=false` 執行。
3. **分批**：`row_cnt` 不超過上表，`timeout_seconds=300`。逾時會整批 rollback，此時把 `row_cnt` 減半重試。
4. **爬完立刻驗可得性**：只看本次 insert 的列的 raw 可用率，不必等整體的 `pop_raw_quality`。
5. **FinMind 先查額度**：先看 `crawl_status(lookback_minutes=60)`。FinMind 系 seed 的 `rows_inserted` 合計超過 200 時，延後這題的 FinMind 補爬。
6. **不動排程**：除非 issue 的修復步驟需要，不呼叫 `crawl_set_schedule`／`crawl_pause`。

## 9. 診斷手冊

### 9.1 症狀 → 第一個要看的證據

| 症狀 | 先看 | 可能層級 |
| --- | --- | --- |
| 主題 seed dry run `pending=0`，`pop` 也沒有該股 | `hidden.<seed>` 有沒有這檔。沒有 → 往上游 seed 查（§8.1）；有但某欄位為 NULL → 被 NOT NULL 條件擋掉 | 上游未爬／L3 |
| raw jsonb 為 NULL | `job_control.http_log` 該 URL 的 `status_code`：404 → API 判定無資料；5xx → API 例外；無紀錄 → 逾時或傳輸錯誤（看 `crawl_runs`） | L1／L0／L6 |
| `found=false` | raw JSON 的 `reason`／`source_errors`。402 或限流 → L6；as_of 早於掛牌日或資料源起點 → L3；上游官網其實有資料 → L1 | L1／L3／L6 |
| raw 可用，但正規化表沒列或欄位 NULL | 對照 raw JSON 結構與正規化 SQL 的 JSON 路徑 | L5 |
| 數值可疑 | raw JSON 本身就錯？是 → 直接呼叫 API、比對上游官網；否 → 正規化 SQL | L1／L5 |
| 時間序列有洞 | `hidden.<seed>` 有沒有產生該格點。有 → 爬取失敗；沒有 → seed 邊界 | L6／L3 |
| 跨來源不一致 | 找第三方（官網）判定誰對；檢查單位與日期對齊（除息日 vs 基準日、as_of vs data_date） | L1／L5／L7 |
| 同一 URL 重複請求多 | `http_stats` 的 `repeated_requests` | L4／L6（S4） |

### 9.2 根因層級

| 層 | 位置 | 典型問題 |
| --- | --- | --- |
| L0 上游 | TWSE／TPEx／MOPS／FinMind／yfinance 本身 | 官網也沒有、格式改版、歷史資料起點 |
| L1 source adapter | `app/*_source.py`、`app/sources.py`、`app/service.py` | parser、單位、民國年、fallback、cache key |
| L2 API route | `app/main.py` | 參數、as_of 回溯、錯誤碼映射、response schema |
| L3 seed 邊界 | `db/poc/*_list.sql` | 格點早於資料源可得日、漏格點、重複格點（rule 13、15、17） |
| L4 raw 呼叫 | `db/poc/raw_*.sql` | URL 參數錯、as_of 取錯日 |
| L5 正規化 | `db/poc/<view>.sql` | JSON 路徑、型別轉換、單位換算、攤平漏列 |
| L6 爬取設定 | cron 排程、`job_control.http_config`、限流 | 402 凍結、逾時、速率 |
| L7 問題本身 | `questions/Q###.md` | 斷言太嚴、誤解欄位語意 |

### 9.3 常用診斷查詢

```sql
-- 某 URL 的實際 HTTP 結果（http_log 只記真實請求，cache 命中不記）
SELECT started_at, status_code, error_message, url FROM job_control.http_log
WHERE url LIKE '%/2330/sbl-history?as_of=2015-08-05%' ORDER BY started_at DESC LIMIT 5

-- cache 內容（2xx、4xx 都會 cache，TTL 見 job_control.http_config 的 cache_ttl_seconds，預設 3600）
SELECT status_code, fetched_at, content FROM job_control.http_cache
WHERE url LIKE '%/2330/sbl-history?as_of=2015-08-05%'

-- seed 母體有沒有這檔、有沒有 NULL 欄位
SELECT * FROM hidden.company_basic_info_list WHERE stk_code = '2454'

-- 某 raw 的可用率依年份分布（找「某年以前全部作廢」的邊界問題）
SELECT extract(year FROM as_of)::int AS yr, count(*) AS total,
       count(*) FILTER (WHERE sbl_history IS NULL OR sbl_history->>'found' = 'false') AS unusable
FROM pop.raw_sbl_history GROUP BY 1 ORDER BY 1
```

直接呼叫 API（從主機打 `localhost`；SQL 內的 URL 是 `host.docker.internal`）：

```bash
curl -s 'http://localhost:5002/api/company/2330/sbl-history?as_of=2015-08-05' | python -m json.tool | head -50
```

改完 SQL、在上 `pop` 前先用 `poc` 驗證時，查詢**務必帶 WHERE 與小 limit**，因為 `poc` view 會即時打 HTTP。

### 9.4 資料血緣速查表

| 主題 | `pop` 正規化表 | raw | seed | API endpoint | 程式 | source 測試（`tests/source/`） |
| --- | --- | --- | --- | --- | --- | --- |
| 日 K | `ohlcv_daily` | `raw_ohlcv_monthly` | `ohlcv_monthly_list` | `/api/ohlcv` | `app/ohlcv_source.py` | `test_ohlcv_stock_month_cache.py` |
| 個股估值 | `company_valuation` | `raw_company_valuation` | `company_valuation_monthly_list` | `/api/company-valuation` | `app/valuation_source.py` | `test_valuation_source.py` |
| 市場估值 | `market_valuation_summary` | `raw_market_valuation_summary` | `market_valuation_date_list` | `/api/market-valuation-summary` | `app/valuation_source.py` | `test_valuation_source.py` |
| 三大法人 | `institutional_net_buy_sell` | `raw_institutional_net_buy_sell` | `institutional_monthly_list` | `/api/institutional-net-buy-sell` | `app/institutional_source.py` | （無，修復時補） |
| 外資持股 | `foreign_ownership` | `raw_foreign_ownership` | `foreign_ownership_monthly_list` | `/api/company/{id}/foreign-ownership` | `app/foreign_ownership_source.py` | `test_foreign_ownership_source.py` |
| 借券 | `sbl_history` | `raw_sbl_history` | `sbl_history_monthly_list` | `/api/company/{id}/sbl-history` | `app/twse_sbl_source.py` | `test_twse_sbl_source.py` |
| 月營收（TWSE／MOPS） | `monthly_revenue_twse` | `raw_monthly_revenue_twse` | `financial_month_twse_list` | `/api/company/{id}/revenue/twse` | `app/sources.py`、`app/service.py` | `test_revenue_twse_source.py` |
| 月營收（FinMind） | `monthly_revenue` | `raw_monthly_revenue` | `financial_month_list` | `/api/company/{id}/revenue` | `app/sources.py`、`app/service.py` | （無） |
| 季財報（MOPS） | `mops_quarterly_financials` | `raw_mops_quarterly_financials` | `mops_quarterly_financials_quarter_list` | `/api/company/{id}/quarterly-financials` | `app/mops_quarterly_financials.py` | `test_mops_quarterly_financials_source.py` |
| 年度財務比率 | `financial_analysis` | `raw_financial_analysis` | `financial_analysis_year_list` | `/api/v1/fundamentals/financial-analysis` | `app/mops_financial_analysis.py` | `test_mops_financial_analysis_source.py` |
| 財報（yfinance） | `financial_yearly_yfinance`、`financial_quarterly_yfinance` | `raw_yearly_financials_yfinance`、`raw_quarterly_financials_yfinance` | `financial_year_yfinance_list`、`financial_quarter_yfinance_list` | `/api/company/{id}/financials/yfinance` | `app/yfinance_source.py`、`app/service.py` | （無） |
| 財報（FinMind） | — | `raw_yearly_financials` | `financial_year_list` | `/api/company/{id}/financials` | `app/sources.py`、`app/service.py` | （無） |
| 股利（FinMind） | `dividend` | `raw_dividend` ← `raw_dividend_history` | `dividend_event_list` ← `company_finmind_list` | `/dividend`、`/dividend/history` | `app/sources.py`、`app/service.py` | （無） |
| 股利（yfinance） | `dividend_yfinance` | `raw_dividend_yfinance` ← `raw_dividend_history_yfinance` | `dividend_event_yfinance_list` ← `company_list` | `/dividend/yfinance`、`/dividend/history/yfinance` | `app/yfinance_source.py`、`app/service.py` | （無） |
| 新聞 | `finmind_news` | `raw_finmind_news` | `finmind_news_monthly_list` | `/api/company/{id}/news/finmind` | `app/finmind_news_source.py` | `test_finmind_news_source.py` |
| 產品營收 | `product_revenue` | `raw_product_revenue` ← `raw_product_revenue_filers` | `product_revenue_filer_list` ← `product_revenue_filer_scope_list` | `/api/company/{id}/product-revenue`、`/api/product-revenue/filers` | `app/sources.py`、`app/service.py` | `test_product_revenue_filers_source.py` |
| 公司基本資料 | `company_basic_info_list`、`company_business_items` | `raw_company_info` | `company_list` | `/api/company/{id}/basic`、`/business-items` | `app/sources.py`、`app/service.py` | （無） |
| 產業鏈 | `chain_info_list` | `raw_chain_info` | `chain_list` | `/api/chain/{ic_code}` | `app/icchain.py` | （無） |

endpoint 測試都在 `tests/endpoint/test_main_endpoints.py`（新聞另有 `test_finmind_news_endpoint.py`）。各 view 的欄位說明見 [`db/poc/README.md`](../db/poc/README.md)。

## 10. 修復與重爬

### 10.1 各層修復流程

| 層 | 改哪裡 | 必跑測試 | 上線方式 | 既有資料處理 |
| --- | --- | --- | --- | --- |
| L0 | 不改資料；上游格式改版就按 L1 處理 | `RUN_UPSTREAM_CONTRACT_TESTS=1 pytest tests/upstream/ -m upstream_contract -q` | — | 寫進問題卡「已知限制」與 `db/poc/README.md` 對應章節 |
| L1／L2 | `app/` | `pytest tests/source/ tests/endpoint/ -q`，並新增重現本 issue 的測試 | 重啟 uvicorn | §10.2 清 cache、刪壞列、重爬 |
| L3 | `db/poc/*_list.sql` | `pytest tests/sql/ -q` | `poc_rebuild_views` → `pop_check_definitions` → `pop_update_mat_views(dry_run=true)` → 執行（seed 只替換 `hidden` view，資料保留） | 新邊界外的舊列：保留或刪除，在 issue 寫明理由 |
| L4／L5 | `db/poc/raw_*.sql`、`db/poc/<view>.sql` | `pytest tests/sql/ -q`；`RUN_POC_VIEW_SELECT_TESTS=1 pytest -m poc_view_select tests/live_sql/ -v` | 同 L3，但 IMMV 會 DROP 再重建，對所有既有 seed 列重打 API | 執行前先估重打成本（見下方注意事項） |
| L6 | 排程、`job_control.http_config` | — | `crawl_set_schedule`／`crawl_pause` | §10.2 重爬被凍結的列 |
| L7 | `questions/Q###.md` | — | — | — |

注意事項：

- **rule 18**（[`db/poc/README.md`](../db/poc/README.md)）：欄位規格會變動時（增刪欄位、換主鍵、換 endpoint），新增 `<name>_v2.sql`，不要就地改寫。只修 bug、規格相容時才就地改。
- **IMMV 重建成本**：`pop_update_mat_views(dry_run=true)` 會列出 `seed_upstreams`。重打 API 次數約等於該 seed 的 `pop_rows`（扣掉 http_cache 命中）。FinMind 系超過 200 列時，先 `crawl_pause` 相關 FinMind 排程並分時段執行，否則 402 會把整批列凍結成 found=false。
- 修改涉及 SQL 通用規則時，同步更新 `db/poc/README.md`。

### 10.2 被凍結的壞資料要重爬

seed 列一旦 insert，下游 IMMV 的那一列就固定了，修好 API 後舊列也不會自己變好。重爬步驟：

1. **清 cache**：刪掉受影響 URL 的 `job_control.http_cache`。2xx 和 4xx 都會被 cache（預設 1 小時），不清的話重爬會拿到舊結果；5xx 不會被 cache。
2. **刪除壞列**：從 `pop.<seed>` 刪掉受影響的 seed 列，pg_ivm 會同步刪除下游 IMMV 的對應列。刪上游 seed（如 `company_list`）**不會**連動刪下游 seed 表（`company_basic_info_list` 是獨立的表）。
3. **重爬**：`crawl_insert_filtered`（同 §8.3 預算）。
4. **重跑**：重跑 `found_by` 所列問題的斷言，結果寫回 issue。

### 10.3 需要補的工具（由指令 0 建立）

目前 MCP 的 `select` 是唯讀，沒有刪列和清 cache 的 tool。指令 0 要在 [`server.py`](../server.py) 新增兩個寫入 tool，風格比照既有的 `crawl_insert_filtered`：預設 `dry_run=true`，回傳會影響的列數與 SQL。

| Tool | 行為 | 護欄 |
| --- | --- | --- |
| `crawl_delete_rows(table, filters, dry_run=true)` | `DELETE FROM pop.<seed> WHERE <filters>` | 必須是 seed table；`filters` 不可為空；回傳下游受影響的 IMMV；單次上限 500 列 |
| `http_cache_evict(url_like, dry_run=true)` | `DELETE FROM job_control.http_cache WHERE url LIKE <url_like>` | `url_like` 必須含 `/api/` 路徑片段與至少一個股票代號或日期，避免清空整個 cache |

建好後，先用 1 列實測：刪除一個 seed 列時，pg_ivm 的維護是否會觸發 API 呼叫（比對刪除前後的 `http_stats`）。把結果寫進本節。

工具建好前的替代做法（手動，需要使用者同意）：

```bash
docker exec -it postgres-db psql -U postgres -d app_db \
  -c "DELETE FROM job_control.http_cache WHERE url LIKE '%/2330/sbl-history?as_of=2015-%'"
```

### 10.4 護欄

- 不 `DROP SCHEMA pop`、不 truncate seed 表；只刪 issue 中列舉的列。
- 所有寫入 tool 先 dry run，並把 dry run 的數字寫進 issue。
- 程式修改在 feature branch 上進行。跑完測試後回報 diff 與測試輸出，**等使用者確認再 commit**。
- 一個 issue 對應一個根因與一個修復範圍。修復時順手發現的其他問題另開 issue。
- 不為了讓斷言通過而放寬斷言（見 §1）。

## 11. AI 指令

把下列文字貼給 Claude Code（在本專案目錄）。`<...>` 換成實際值。

### 指令 0：初始化（只做一次）

```text
依 verify_questions/README.md §3 建立資料夾骨架（LEDGER.md 依 §6.4、questions/ runs/ issues/ checks/ snapshots/），
再依 §10.3 在 server.py 新增 crawl_delete_rows 與 http_cache_evict 兩個預設 dry_run 的 MCP tool，並補上測試。
完成後用 1 列實測刪除 seed 列是否觸發 API 呼叫，把結果寫回 README §10.3。
回報 diff 與測試結果，不要 commit。
```

### 指令 1：盤點

```text
依 verify_questions/README.md §4 步驟 1 盤點資料庫：呼叫 crawl_pop_tables、pop_raw_quality、
crawl_status(lookback_minutes=1440)、http_stats(minutes=1440)，存成 snapshots/<今天>.json。
與上一份 snapshot 比較，更新 LEDGER.md 的品質趨勢與覆蓋矩陣，
並列出：usable% 下降的 raw、沒有任何問題覆蓋的 pop 表、可疑的新現象（各附一句證據）。
```

### 指令 2：出題

```text
依 verify_questions/README.md §5 出 <N> 題新問題卡（status: draft），格式照 §6.1。
優先覆蓋 LEDGER 覆蓋矩陣中沒被覆蓋的 pop 表，以及 §12 的候選 issue。
每題至少一個跨來源或內部一致性斷言、至少一個邊界案例，補爬量要在 §8.3 預算內。
每句 SQL 都先用 select 確認可執行。寫完列出清單（標題、策略、覆蓋的表、預估補爬列數）給我審，不要補爬。
```

### 指令 3：執行問題

```text
依 verify_questions/README.md §4 步驟 3–5 執行 <Q###>：
先 select 現有資料 → 依 §8 補爬（先 dry run，遵守 §8.1 依賴鏈與 §8.3 預算）→ 跑決策 SQL → 跑全部斷言。
寫 runs/<今天>_<Q###>.md（格式 §6.2），更新問題卡的 status、last_run、歷次結果，以及 LEDGER。
斷言失敗依 §7.3 分流，需要時開 issue（格式 §6.3）。不要修改程式。
```

### 指令 4：診斷

```text
依 verify_questions/README.md §9 診斷 <I###>：
用 §9.1 的證據順序收集證據（http_log、http_cache、hidden、直接呼叫 API、對照 raw JSON 與正規化 SQL、必要時對照上游官網），
判定根因層級 L0–L7，把證據、根因、修復方案寫回 issue。修復方案要包含：改哪些檔、是否需要 _v2 SQL（rule 18）、
影響多少既有列、預估重打 API 次數。status 改為 diagnosed。只讀，不改程式，也不寫 DB。
```

### 指令 5：修復

```text
依 verify_questions/README.md §10 修復 <I###>（照 issue 裡已寫好的方案）：
開 feature branch 修改 → 補上能重現本 issue 的測試 → 跑該層的必跑測試 → 上線（重啟 API 或重建 view／IMMV，先 dry run）→
依 §10.2 清 cache、刪壞列、重爬 → 重跑 found_by 所列問題的斷言。
每一步的實際數字寫回 issue；斷言全部通過才把 status 改為 closed。回報 diff 與測試結果，等我確認再 commit。
```

### 指令 6：回歸

```text
依 verify_questions/README.md §4 步驟 7 回歸：對所有 status 為 passing 的問題只跑斷言（不補爬）。
變成 FAIL 的問題改為 failing，並依 §7.3 開 issue 或附加到既有 issue。在 LEDGER 品質趨勢記一行。
```

### 指令 7：一輪完整循環

```text
依 verify_questions/README.md 跑一輪：
指令 1 → 挑一題（優先順序：failing > 最久未跑的 draft > 最久未跑的 passing）執行指令 3 → 對新開的 issue 執行指令 4 →
draft 少於 3 題時執行指令 2 補 3 題。補爬遵守 §8.3。不要執行指令 5，把建議修復的 issue 依 §7.4 嚴重度排好列給我。
```

### 持續執行

指令 7 只補爬、只寫 `verify_questions/` 內的檔案，不改程式，所以適合排程：

```text
/loop 3h 依 verify_questions/README.md 執行指令 7
```

注意：主機睡眠時 Docker 會暫停，迴圈與 cron 都會停（長時間跑可用 `caffeinate -s`）。FinMind 額度與 cron 共用，迴圈間隔不要短於 1 小時。

## 12. 現況基線（2026-11-24）

撰寫本文件時，透過 MCP 對 live DB 觀察到的現況。這些可以作為第一批 issue 與問題的素材。

| 觀察 | 證據 | 初步判斷（待指令 4 確認） |
| --- | --- | --- |
| `raw_sbl_history` usable 僅 40.5%（200／336 列 NULL） | NULL 全是 HTTP 404，集中在 2005-01 ～ 2015-08；2330 在 2015-09 之後 0 列 NULL | seed 起點早於 API 實際可得資料（L3），或 backend 處理舊年份有誤（L1）。屬 S4，但若 L1 則可能是 S2 |
| `raw_monthly_revenue_twse` 有 111 列 found=false | 全部是 as_of 2010-01 ～ 2013-01；2013-02 之後全部可用 | 同上：seed 邊界（L3）或 MOPS t21sc03 舊格式（IFRS 前）parser 不支援（L1） |
| `raw_company_info` 有 187 列 found=false（17.7%） | 這些公司進不了 `company_basic_info_list`，所有主題 seed 都不會有它們 | 需要確認是興櫃／下市（L0，已知限制）還是漏抓（L1） |
| `raw_finmind_news` usable 72.6%（26 列 NULL） | — | 待診斷（FinMind 限流 L6？） |
| `company_valuation` 有 12 列 data_date 落後 as_of 超過 7 天 | 全是 as_of 2022-02-05 → data_date 2022-01-26 | **不是資料問題**：春節休市（L7）。斷言門檻應為 12 天，如 Q001 C4 |
| 2454、1101 不在 `company_list` | `company_list` 目前 1,055／約 2,250 家 | 不是品質問題，是還沒爬到；執行問題時依 §8.1 由上往下補 |
| 2317 近 3 年估值只有 13／37 個月 | `pop.company_valuation` | 還沒爬到；Q001 第一次執行時補爬即可 |

已驗證通過（可作為回歸基準）：`ohlcv_daily` 的 OHLC 合理性與主鍵唯一性；估值收盤價 vs 日 K 收盤價（543 列可比對、0 違規）；FinMind vs yfinance 現金股利（26 列可比對、0 違規）。
