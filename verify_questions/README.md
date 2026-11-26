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

五個原則：

- **斷言就是 SQL**：每個斷言是一句可以直接丟給 MCP `select` 的 `SELECT`，回傳「違規列」，0 列＝通過。
- **補爬是按需的**：只爬問題需要的列，不等 cron 輪到，也不浪費 API 額度。
- **修復要閉環**：程式改好不算完。seed 列一旦 insert，IMMV 結果就固定了，所以還要清 cache、刪壞列、重爬，再重跑原本失敗的斷言，issue 才能關。
- **不為了通過而放寬斷言**：斷言只有在能說明「現實就是如此」時才可以改（例：春節休市），而且要寫進問題卡的「已知限制」。
- **品質維度照 DAMA**：斷言依 DAMA-DMBOK2 的六個核心維度（完整、唯一、及時、有效、準確、一致）加三個延伸維度（精確、可取得、合理）分類，一般定義見 [`DATA_QUALITY_DIMENSIONS.md`](DATA_QUALITY_DIMENSIONS.md)，在本專案的意思與檢核手法見 §7.1。LEDGER 用「表 × 維度」矩陣追蹤哪些格子還沒驗過。沒驗過不等於沒問題：Q001–Q008 的 39 個斷言只有 1 個驗唯一性，2026-11-26 補盤一次就在 4 張表找到重複（§12.1）。

```
            ┌───────────────── 指令 1 盤點 ─────────────────┐
            ▼                                               │
   snapshots/ ＋ LEDGER 維度矩陣                              │
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

沒通過時：

- **API 沒在跑**：在 repo 根目錄以背景方式執行 `.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 5002`，再確認 health 回 200。
- **MCP 連不上**：`server.py` 的 tool 也可以直接呼叫，結果相同（`run_checks.py`、`snapshot.py` 就是這樣做的）：`.venv/bin/python -c "import server; print(server.select('SELECT 1'))"`。寫入類 tool（`crawl_insert_filtered` 等）同樣先 dry run。在 run 紀錄的「前置條件」寫明用的是哪一種。
- **API 跑的是工作目錄的程式**：`git status` 顯示 `app/` 有未 commit 的改動時，先看 diff。補爬會把當下 API 的回應凍結進 IMMV，所以不要補爬受這些改動影響的 endpoint，並寫進 run 紀錄。

## 3. 資料夾結構

```
verify_questions/
├── README.md                   # 本文件：方法與 AI 指令
├── DATA_QUALITY_DIMENSIONS.md  # DAMA 資料品質維度的一般定義（§7.1 的依據）
├── LEDGER.md                   # 總帳：問題狀態、issue 清單、維度矩陣、剖析基線、品質趨勢
├── questions/                  # 問題卡，一題一檔
│   └── Q001-valuation-per-percentile.md
├── runs/                       # 每次執行一份：補爬紀錄、斷言結果、維度結論、決策資料、結論
│   └── 2026-11-24_Q001.md
├── issues/                     # 資料品質問題，一個根因一檔
│   └── I001-sbl-history-pre2015-404.md
├── checks/                     # 可重用的斷言模板（Jinja 參數，同 db/poc 的 {{ schema }} 慣例）
│   └── ohlcv_daily/ohlc_consistency.sql
├── snapshot.py                 # 指令 1 盤點：四個 MCP 呼叫＋維度剖析（§7.5）
├── run_checks.py               # 執行問題卡的決策 SQL 與斷言
├── retry_failed.py             # 重爬被凍結的上游失敗列（§10.5）
└── snapshots/                  # 每次盤點的 MCP 原始輸出與剖析
    └── 2026-11-24.json
```

- 問題用 `Q###`、issue 用 `I###`，三位數遞增、不重用；檔名的 slug 用英文小寫。
- `checks/` 的模板用 `{{ stk_codes }}`、`{{ since }}` 參數。問題卡裡放的是代入參數後、可以直接執行的 SQL。

## 4. 系統循環

| 步驟 | 做什麼 | 用到的 MCP tool | 產出 |
| --- | --- | --- | --- |
| 1 盤點 | 拍下資料庫現況，和上一份比較（`.venv/bin/python verify_questions/snapshot.py` 一次做完四個呼叫與 §7.5 維度剖析，並印出比較） | `crawl_pop_tables`、`pop_raw_quality`、`crawl_status(1440)`、`http_stats(1440)`、`select` | `snapshots/<日期>.json`、LEDGER 品質趨勢、維度矩陣、剖析基線 |
| 2 出題 | 依維度矩陣的空格、覆蓋缺口與策略維度（§5）寫問題卡 | `describe_table`、`select`（確認欄位與現有資料） | `questions/Q###.md`（`status: draft`） |
| 3 補爬 | 依問題卡的 `crawl` 區塊補資料（規則見 §8） | `select` 看現有 → `crawl_insert_filtered` dry run → 實際寫入 | run 紀錄的「補爬」表 |
| 4 取數 | 執行問題卡的決策 SQL | `select` | run 紀錄的「決策資料」與結論 |
| 5 驗證 | 逐一執行斷言，判定 PASS / FAIL / INCONCLUSIVE（`.venv/bin/python verify_questions/run_checks.py Q001 --decision` 一次跑完決策 SQL 與所有斷言，每個斷言旁印出它的 DAMA 維度；印成 `[?]` 代表標籤不在 §7.1，要改），再依維度彙整 | `select` | run 紀錄的「斷言結果」與「維度結論」；失敗依 §7.3 分流 |
| 6 診斷修復 | 根因分析（§9），修程式並閉環（§10） | `select`（含 `job_control.*`、`hidden.*`）、`poc_rebuild_views`、`pop_check_definitions`、`pop_update_mat_views`、`crawl_delete_rows`、`http_cache_evict`、`crawl_insert_filtered` | `issues/I###.md` 更新、程式 diff、測試結果 |
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
- **至少一道錯值防線**：至少一個斷言要能抓到「有值但錯」（S1）：`accuracy`（對照管線以外的權威參考值），或兩邊**獨立**的 `cross-source`／`internal`／`temporal`。獨立的意思是兩邊的值不是同一段程式由對方算出來的，寫之前先查 §9.4 的程式（§7.2）。反例：`company_valuation` 的 `eps_ttm`、`bvps`、`dps` 是 adapter 由 PER、PBR、殖利率反推的，拿來互推 PER 必定成立，什麼錯都抓不到。資料庫裡真的沒有獨立來源時（例：Q006 的大盤 PER 全由同一份個股資料加總），題目仍可 `passing`，但要在已知限制寫「沒有錯值防線」與候選來源，run 的維度結論中準確性寫「未驗證」，投資面結論也要寫出這點。
- **至少 3 個 DAMA 維度**：斷言標題以 §7.1 的標籤開頭；一題至少涵蓋 3 個維度，其中要有完整性。
- **一批題目要填矩陣空格**：指令 2 一次出的題目，合計至少要填 LEDGER 維度矩陣的 2 個空格，準確性與唯一性優先。六個核心維度在 `passing` 的題目中都要至少出現一次。
- **斷言數 3–8 個**：同一個標籤最多一個斷言，避免重複（比對範圍不同時例外，在標題寫明）。

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

以下範例的 SQL 已於 2026-11-24 在 live DB 驗證可執行。C2、C4 於 2026-11-26 依 §7.2 的獨立性規則改寫，也已驗證（C4 可比對 34 列、0 違規）。

````markdown
---
id: Q001
title: 2330、2317 目前本益比在自身近 3 年的第幾百分位？
strategy: valuation          # §5.1 的維度
status: draft                # draft | passing | failing | blocked
stocks: ["2330", "2317"]
window: {since: 2023-11-01, until: 2026-11-24}
pop_tables: [company_valuation, ohlcv_daily, mops_quarterly_financials]
crawl:                       # 步驟 3 依序執行；budget = 本題此 seed 最多補幾列
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["2330", "2317"]}
    order_by: ["valuation_date"]
    budget: 72               # 3 年 × 12 月 × 2 檔
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["2330", "2317"]}
    order_by: ["month_start_date"]
    budget: 24
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["2330", "2317"]}
    order_by: ["quarter_end"]
    budget: 32               # 3 年 × 4 季 × 2 檔，加上窗口前 4 季（C4 要近四季合計）
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

### C2 validity — PER、PBR、殖利率在合理範圍
```sql
SELECT stk_code, as_of, per, pbr, dividend_yield_pct
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317') AND as_of >= DATE '2023-11-01'
  AND (per <= 0 OR per > 300 OR pbr <= 0 OR pbr > 50
       OR dividend_yield_pct < 0 OR dividend_yield_pct > 25)
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

### C4 cross-source — 估值隱含 EPS（收盤價 ÷ PER，TWSE BWIBBU）= MOPS 近四季單季 EPS 合計（誤差 2% 或 0.05 元，排除 3 月取樣）
```sql
WITH q AS (
  SELECT DISTINCT stk_code, fiscal_year, quarter, eps, available_date
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2317') AND eps IS NOT NULL
), m AS (
  SELECT v.stk_code, v.as_of, v.eps_ttm, s.n_q, s.span, s.mops_4q
  FROM pop.company_valuation v
  CROSS JOIN LATERAL (
    SELECT count(*) AS n_q, sum(eps) AS mops_4q,
           max(fiscal_year * 4 + quarter) - min(fiscal_year * 4 + quarter) AS span
    FROM (SELECT fiscal_year, quarter, eps FROM q
          WHERE q.stk_code = v.stk_code AND q.available_date <= v.as_of
          ORDER BY fiscal_year DESC, quarter DESC LIMIT 4) last4) s
  WHERE v.stk_code IN ('2330','2317') AND v.as_of >= DATE '2023-11-01'
    AND v.eps_ttm IS NOT NULL AND extract(month FROM v.as_of) <> 3
)
SELECT stk_code, as_of, round(eps_ttm, 3) AS twse_implied_eps, mops_4q
FROM m WHERE n_q = 4 AND span = 3 AND abs(eps_ttm - mops_4q) > greatest(0.05, 0.02 * abs(mops_4q))
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
WITH q AS (
  SELECT DISTINCT stk_code, fiscal_year, quarter, eps, available_date
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2317') AND eps IS NOT NULL
), m AS (
  SELECT v.stk_code, v.as_of, s.n_q, s.span
  FROM pop.company_valuation v
  CROSS JOIN LATERAL (
    SELECT count(*) AS n_q, max(fiscal_year * 4 + quarter) - min(fiscal_year * 4 + quarter) AS span
    FROM (SELECT fiscal_year, quarter FROM q
          WHERE q.stk_code = v.stk_code AND q.available_date <= v.as_of
          ORDER BY fiscal_year DESC, quarter DESC LIMIT 4) last4) s
  WHERE v.stk_code IN ('2330','2317') AND v.as_of >= DATE '2023-11-01'
    AND v.eps_ttm IS NOT NULL AND extract(month FROM v.as_of) <> 3
)
SELECT count(*) FROM m WHERE n_q = 4 AND span = 3
```

### C5 timeliness — 實際資料日與取樣日差距 ≤ 12 天
```sql
SELECT stk_code, as_of, data_date, as_of - data_date AS lag_days
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317') AND data_date < as_of - 12
```

## 已知限制
- 虧損期間 PER 為 NULL，這是定義上的空值，不是缺值；百分位只算非 NULL 月份。
- `eps_ttm`、`bvps`、`dps` 是 adapter 由 PER、PBR、殖利率反推的（`app/valuation_source.py`），彼此互推必定成立，所以不拿來寫一致性斷言。C4 改和 MOPS 季報比：不同發布者、不同 adapter。
- C4 排除 3 月取樣：公司常在 3/31 期限前就公告年報，TWSE 會先換 EPS 基準，本系統的 PIT 則以期限為準。2330 在 2020–2026 每年 3 月的取樣都對不上，其他月份 0 違規。
- C5 容忍 12 天：2022-02-05 取樣日的實際資料日是 2022-01-26（春節休市 10 天）。

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
| 斷言 | 維度 | 結果 | 違規列數 | 可比對列數 | 說明 |
| --- | --- | --- | ---: | ---: | --- |
| C1 | 完整性 | PASS | 0 | — | |

## 維度結論
（九個維度各一行：PASS／FAIL／未驗證。未驗證要寫原因；只有推導欄位互推、沒有獨立比對的，寫「未驗證」）
| 維度 | 狀態 | 依據 |
| --- | --- | --- |
| 準確性 | 未驗證 | 沒有 accuracy 斷言；C4 是獨立來源互證，屬一致性 |

## 準確性抽查（決策結論會觸發動作時才做）
| 值 | DB | 權威來源（URL、取得時間） | 權威值 | 結果 |
| --- | --- | --- | --- | --- |

## 結論
- 投資面：（依決策情境給出結論；資料有 FAIL 時明寫「結論不可信」；列出決策用到、但狀態是「未驗證」的維度）
- 品質面：新開 issue／附加到既有 issue／修改斷言（附理由）
````

### 6.3 Issue `issues/I###-<slug>.md`

````markdown
---
id: I001
title: raw_sbl_history 在 2015-09 以前全部 404
severity: S4                 # §7.4
dimension: 可取得性           # 違反的 DAMA 維度（§7.1）
layer: null                  # 診斷後填 L0–L7（§9.2）
status: open                 # open | diagnosed | fixing | verifying | closed | wontfix
pop_tables: [raw_sbl_history, sbl_history]
seeds: [sbl_history_monthly_list]
found_by: [Q005]
---

## 症狀（附可重現的 SQL）
## 證據（http_log、直接呼叫 API 的結果、上游官網對照）
## 根因
## 同類掃描（同一個失效模式寫成 SQL 掃所有 pop 表的結果；不同根因另開 issue）
## 修復方案（改哪些檔、要不要新增 _v2 SQL、影響多少既有列、預估重打 API 次數）
## db/poc 規則檢查（修復方案動到 SQL 時必填，逐條對照 db/poc/README.md，見 §10.1）
## 修復紀錄（diff 摘要、測試結果）
## 重爬與驗證（http_cache_evict／crawl_delete_rows／crawl_insert_filtered 的實際數字、重跑的斷言結果）
````

### 6.4 `LEDGER.md`

```markdown
# Ledger

## 問題
| ID | 策略 | 標題 | 狀態 | 最後執行 | 未結 issue |

## Issues
| ID | 嚴重度 | 維度 | 層級 | 標題 | 狀態 | 發現於 | 修復 commit |

## 維度矩陣（pop 表 × DAMA 維度）
| pop 表 | 策略 | 完整 | 唯一 | 及時 | 有效 | 準確 | 一致 | 精確 | 可取得 | 合理 |
（列出所有 pop 正規化表。格子寫最近一次的斷言結果與出處，例：`Q001-C1 ✓`、`Q005-C1 ✗`；
只有 §7.5 剖析涵蓋、沒有斷言時寫 `剖析`；推導欄位互推不算；都沒有就寫 `—`。
`—` 是下一輪出題的目標，準確與唯一優先）

## 剖析基線（§7.5）
| pop 表 | 欄位或實體鍵 | 現況 | 分類 | 依據 |
（部分空值欄位、全空欄位、實體重複各一行。分類：結構性空值／合法無資料／缺陷（附 issue）。盤點只報告相對這張表的變化）

## 品質趨勢（每次盤點一行）
| 日期 | raw 平均 usable%（可取得） | 最低三個 raw（usable%） | 全空欄位數（完整） | 實體重複列數（唯一） | 新開／關閉 issue |
```

`snapshots/<日期>.json` 的格式是 `{"taken_at": ..., "crawl_pop_tables": [...], "pop_raw_quality": [...], "crawl_status": [...], "http_stats": {...}, "profile": {...}}`。前五項是各 tool 的原始輸出；`profile` 是 §7.5 的剖析：`fields`（每表 `rows` 與每欄 `non_null` 列數）、`entity_dups`（每表的實體鍵、`keys` 重複組數、`extra_rows` 多出的列數）、`latest`（每表最新資料日）。

## 7. 品質斷言

### 7.1 品質維度（DAMA）

維度採用 DAMA-DMBOK2 的六個核心維度加三個延伸維度，一般定義見 [`DATA_QUALITY_DIMENSIONS.md`](DATA_QUALITY_DIMENSIONS.md)。下表把每個維度落到本專案：「在本專案問的是」是它在這個資料庫裡的意思；「標籤」是斷言標題開頭要寫的字，一個標籤只屬於一個維度；「常見根因」是 §9 診斷的起點。

| 維度 | 在本專案問的是 | 標籤 | 範例（回傳違規列） | 常見根因 |
| --- | --- | --- | --- | --- |
| 完整性 Completeness | 期望的每個格點都有列，而且有列的必要欄位有值。`found=true` 也可能整欄是空的（I010、I016），所以要看欄位，不能只看 found | `completeness`（格點）、`field`（欄位） | 格點：`generate_series` 產生期望格點 `LEFT JOIN` 實際表，取 NULL（§6.1 C1）。欄位：`SELECT stk_code, as_of FROM pop.financial_quarterly_yfinance WHERE stk_code IN (...) AND eps_ttm IS NULL` | L3 seed 漏格點；L1 把失敗當成功；L0 來源本來就沒有 |
| 唯一性 Uniqueness | 同一個實體（交易日、事件、季度、新聞、借券交易）只算一次。有唯一索引的表主鍵不會重複，要查的是**實體鍵**：同一事實被兩個 as_of 取到、上游清單重複、相鄰取樣窗口重疊。重複會讓百分位、次數、加總多算 | `uniqueness` | `SELECT stk_code, fiscal_year, quarter, array_agg(as_of ORDER BY as_of) FROM pop.mops_quarterly_financials WHERE fiscal_year IS NOT NULL GROUP BY 1, 2, 3 HAVING count(*) > 1`。各表的實體鍵見 `snapshot.py` 的 `ENTITY_KEYS` | L3 兩個格點對到同一期；L5 攤平沒去重；L0 上游重複 |
| 及時性 Timeliness | 決策時點拿得到，而且夠新：不早於公告（前視偏誤）、資料日不落後取樣日太多、最新一期已經進來 | `point-in-time`、`timeliness`、`freshness` | PIT：`available_date > as_of`（Q002 C3）。落後：`WHERE data_date < as_of - 12`。最新一期：見下方 freshness | L2／L3 as_of 設計；還沒爬；L0 上游晚公告 |
| 有效性 Validity | 格式、型態、代碼、數值範圍符合規格 | `validity` | `WHERE per <= 0 OR per > 300 OR pbr <= 0 OR pbr > 50`（§6.1 C2）；`WHERE stk_code !~ '^[0-9]{4}[0-9A-Z]{0,2}$'` | L1 parser；L5 型別轉換 |
| 準確性 Accuracy | 值和現實相符。只有對照**管線以外**的權威參考值才算：公司公告、年報、官網頁面上的 golden 值，或指令 3 的官網抽查。兩個來源一致屬於一致性，因為兩邊可能同錯 | `accuracy` | golden 值骨架見下方 | L1 單位、欄位對錯；L0 上游錯 |
| 一致性 Consistency | 同一事實在同表欄位、不同來源、不同時間點之間一致，單位與定義相同。兩邊必須獨立（§7.2） | `internal`、`cross-source`、`temporal`、`unit` | internal：三大法人合計（上游提供）= 各法人加總（Q004 C2）。cross-source：估值隱含 EPS = MOPS 近四季 EPS（§6.1 C4）。temporal：TTM = 分次抓到的 12 個月加總（Q002 C6）。unit：季營收 vs 三個月月營收合計，比值偏離 1 一個數量級以上就算違規 | L1 單位、民國年；L5 日期對齊；raw 與正規化分歧（I011） |
| 精確性 Precision | 細緻度足以支撐決策與門檻：價格落在升降單位上，金額單位的捨入不會吃掉比對門檻 | `precision` | 升降單位：見下方 | L5 型別（numeric 轉 int）；上游四捨五入；還原價混入 |
| 可取得性 Accessibility | 需要時拿得到：補爬的列 raw 可用，上游沒有封鎖或限流 | `availability` | `SELECT stk_code, as_of FROM pop.raw_company_valuation WHERE stk_code IN (...) AND (company_valuation IS NULL OR company_valuation->>'found' = 'false')`。盤點另看 `pop_raw_quality` 與 `http_stats` 的 5xx | L6 限流（402 凍結）；L0 封鎖；L3 打了必定沒資料的格點（S4） |
| 合理性 Reasonableness | 欄位間的邏輯與常理：日期先後、物理關係、時間序列跳動在交易制度的限制內 | `reasonableness` | OHLC：`WHERE high < GREATEST(open, close) OR low > LEAST(open, close)`。漲跌幅：見下方 | L1 欄位錯位、日期錯置；L0 減資、除權（寫進已知限制） |

既有問題卡的標籤照舊有效（`availability` 歸可取得性）。只檢查「欄位有值」的斷言改用 `field`（例：Q007 C2）。

以下範例於 2026-11-26 在 live DB 驗證。

**freshness**：只拿「最新一筆」做決策的題目要寫（例：目前的 PER）。completeness 的格點畫到 `CURRENT_DATE` 時，已經涵蓋最新一期。下例檢查月營收最新的取樣日是不是最近一個已過的 10 日，0 違規：

```sql
SELECT s.stk_code, max(r.as_of) AS latest_as_of,
       (date_trunc('month', CURRENT_DATE - 9) + INTERVAL '9 days')::date AS expected_as_of
FROM unnest(ARRAY['2330','2454','6488']) AS s(stk_code)
LEFT JOIN pop.monthly_revenue_twse r ON r.stk_code = s.stk_code AND r.latest_month_value IS NOT NULL
GROUP BY s.stk_code
HAVING max(r.as_of) IS DISTINCT FROM (date_trunc('month', CURRENT_DATE - 9) + INTERVAL '9 days')::date
```

**precision**：收盤價要落在升降單位上（只套 4 碼股票，ETF 規則不同）。2010–2026 約 2.3 萬列，0 違規；混入還原價或浮點誤差時會違規：

```sql
SELECT stk_code, trade_date, close
FROM pop.ohlcv_daily
WHERE close IS NOT NULL AND stk_code ~ '^[1-9][0-9]{3}$'
  AND close % CASE WHEN close < 10 THEN 0.01 WHEN close < 50 THEN 0.05 WHEN close < 100 THEN 0.1
                   WHEN close < 500 THEN 0.5 WHEN close < 1000 THEN 1 ELSE 5 END <> 0
```

**reasonableness**：單日漲跌不超過漲跌停（2015-06-01 起 10%，之前 7%）。用 `change` 推前一日參考價，不受資料缺口影響；除權息日 TWSE 標 `X`，`change` 解析成 NULL，自動排除。全表 0 違規。新上市前 5 日沒有漲跌幅限制，碰到要寫進已知限制：

```sql
SELECT stk_code, trade_date, close, change, round(100 * change / (close - change), 2) AS pct
FROM pop.ohlcv_daily
WHERE change IS NOT NULL AND close - change > 0
  AND abs(change) > (CASE WHEN trade_date >= DATE '2015-06-01' THEN 0.10 ELSE 0.07 END) * (close - change) + 0.01
```

**accuracy**（骨架，不能直接執行）：golden 值由出題的 AI 從管線以外的權威來源取得，每列在註解寫出處與取得日期。不可從 `pop` 抄，否則只是拿自己比自己：

```sql
WITH golden(stk_code, year, eps) AS (VALUES
  ('<代號>', <年度>, <每股盈餘>)   -- 出處：<年報或官網頁面>，取得於 <日期>
)
SELECT g.stk_code, g.year, g.eps AS golden_eps, f.eps AS db_eps
FROM golden g LEFT JOIN pop.financial_analysis f USING (stk_code, year)
WHERE f.eps IS NULL OR abs(f.eps - g.eps) > 0.005
```

### 7.2 寫法規範

- 單一 `SELECT` 或 `WITH`，不可含 `;`（MCP `select` 的限制）。可以讀 `pop`、`hidden`、`job_control`。
- 送進 `select` 前去掉 `--` 註解：`select` 會把 SQL 包成子查詢，結尾的註解會吃掉外層的 `) LIMIT`。`checks/` 模板的說明寫在註解裡，代入參數後要先去掉。
- 回傳的欄位要足以直接診斷：主鍵、日期，以及出問題的值本身，不要只回 `count(*)`。
- 標題寫成 `### Cn <標籤> — <說明>`，標籤取自 §7.1。
- 比對類斷言（`internal`、`cross-source`、`temporal`、`unit`、`accuracy`）都要另附「可比對列數」查詢。可比對列數為 0 時，結果是 **INCONCLUSIVE**，不是 PASS。
- **兩邊要獨立**：比對類斷言的兩邊，不能有一邊是同一段程式由另一邊算出來的，否則必定成立。寫之前查 §9.4 的程式與 adapter 原始碼。已知的推導欄位：`company_valuation` 的 `eps_ttm`＝收盤價 ÷ PER、`bvps`＝收盤價 ÷ PBR、`dps`＝收盤價 × 殖利率；`market_valuation_summary` 的 `market_per`、`market_pbr`、`market_dividend_yield_pct` 由同表的加總欄位算出（都在 `app/valuation_source.py`）。這類比對只是算術檢查，不算任何維度的證據，也不填進維度矩陣。
- 斷言只讀 `pop`，不讀 `poc`。`poc` view 每次查詢都會打 HTTP，結果不穩定，也會消耗額度。
- **日期用台北時間**：DB 時區是 UTC，台北時間 00:00–08:00 的 `CURRENT_DATE` 還是前一天。freshness、PIT 這類跟「今天」比的斷言，用 `(now() AT TIME ZONE 'Asia/Taipei')::date`（例：Q011 C6）。只是畫格點上限的 `CURRENT_DATE` 差一天沒有影響，可以不改。
- 門檻要寫出理由（例如 C5 的 12 天＝春節最長休市），放在斷言標題或「已知限制」。兩邊的單位或小數位不同時，門檻至少是較粗那邊的半個單位（例：百萬元對千元，誤差下限是 0.5 百萬元），小公司用百分比門檻時要特別檢查。
- **規則要先對全表跑一次**：新規則先不限股票對全表執行，確認違規列不是「現實就是如此」。例：「交易日不是週末」不成立，`ohlcv_daily` 有 83 個 2010–2018 年的週六補行交易日。

### 7.3 斷言失敗後的分流

1. **排除暫時性**：同一斷言重跑一次。若是爬取當下上游 5xx 或逾時，重跑就會通過；記在 run 的說明欄，不開 issue。
2. **判斷是否現實如此（L7）**：休市、虧損 PER 為 NULL、金融股沒有毛利率、新上市資料不足。這類情況修改斷言或縮小範圍，並寫進「已知限制」，不開 issue。
3. **其餘開 issue**：先搜尋 `issues/`，看有沒有相同 `pop_tables` 與症狀的未結 issue。有就把本題加進該 issue 的 `found_by`，沒有才開新的。issue 的 `dimension` 填違反的 DAMA 維度。問題卡的 `status` 改為 `failing`，`issues` 欄加上 issue 編號。
4. **資料拿不到（L0 或額度）**：`status: blocked`，寫明阻擋原因與解除條件（例：「等 FinMind token」）。

### 7.4 嚴重度（修復順序 S1 → S4）

| 等級 | 定義 | 為什麼排這個順序 |
| --- | --- | --- |
| S1 錯值 | 有值但錯（單位、日期錯位、張冠李戴） | 看起來正常，會直接誤導決策，最危險 |
| S2 缺值 | NULL、found=false、時間序列有洞 | 看得出來，但會讓結論偏誤（例：百分位少算月份） |
| S3 過時 | 資料日落後太多、未更新 | 影響時效型決策 |
| S4 浪費 | 必定作廢的 API 呼叫、重複請求 | 不影響正確性，但拖慢所有 seed 的填滿速度 |

各維度的預設嚴重度（再依實際後果調整）：準確性、一致性、單位、PIT 錯誤，以及會重複計入決策的唯一性問題 → S1；完整性與可取得性的真缺值 → S2；及時性的落後 → S3；必定作廢的呼叫 → S4。有效性、合理性、精確性看後果：值錯歸 S1，值缺歸 S2。

### 7.5 維度剖析（盤點時做，不需要問題）

斷言只驗證問題碰到的列。`snapshot.py` 另對所有正規化 `pop` 表做下列剖析，存在 snapshot 的 `profile`，印出與上一份的差異：

| 維度 | 剖析 | 看什麼 |
| --- | --- | --- |
| 完整性 | 每欄非 NULL 列數（`profile.fields`） | 新的全空欄位、非空率下降 ≥ 1 點。I016 的 4 個 TTM 欄位 0% 非空，`pop_raw_quality` 卻是 100%：只看 found 抓不到 |
| 唯一性 | 依 `ENTITY_KEYS` 的實體鍵算重複（`profile.entity_dups`） | 新出現或變多的重複 |
| 及時性 | 每張表最新的資料日（`profile.latest`） | 跟該表的取樣節奏比；落後是「還沒爬」還是「爬了但沒更新」 |
| 可取得性 | `pop_raw_quality` 的 usable%、`http_stats` 的 5xx | usable% 下降、5xx 集中的 endpoint |

部分空值很常見，多數是合理的（例：虧損期間 PER 為 NULL、2017-12-18 前沒有外資自營欄位）。第一次剖析時要逐欄分類，寫進 LEDGER「剖析基線」：

- **結構性空值**：來源本來就沒有這個欄位，或只在特定情況才有值。寫出依據（adapter 程式或上游文件）。
- **合法無資料**：現實就是如此（虧損、金融股沒有毛利率、新上市）。
- **缺陷**：依 §7.3 開 issue。
- **待查**：查了還分不出是合法還是缺陷時，寫出已知的證據與下一步，下一輪指令 1 優先查。不要為了填滿表格硬歸類。

之後盤點只報告相對基線的變化。新的全空欄位與新的實體重複，預設當作缺陷，直到查明為止。

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
                                             │                           ├─► financial_year_yfinance_list ─► financial_quarter_yfinance_list
                                             │                           ├─► company_finmind_list ─► (raw_dividend_history) ─► dividend_event_list
                                             │                           └─► (raw_dividend_history_yfinance) ─► dividend_event_yfinance_list
                                             └─► (raw_company_info：判定是否上市櫃；I015 起兩條股利歷史都改從 company_basic_info_list 出發)
product_revenue_filer_scope_list ─► (raw_product_revenue_filers) ─► product_revenue_filer_list
market_valuation_date_list（市場層級，無個股上游）
```

例：要分析 2454，先做 `company_list {stk_code: "2454"}`，再做 `company_basic_info_list {stk_code: "2454"}`，最後才做主題 seed。

seed 的 INSERT 要求**所有欄位 NOT NULL**（[`pipeline.py` `_build_seed_insert_sql`](../pipeline.py)）。只要 `hidden.<seed>` 的任一欄位為 NULL，該列永遠不會被選進 `pop`，下游也就不會有這檔股票。

### 8.2 seed 速查表

| seed | 常用 filter 欄位 | 時間欄（`order_by`） | 下游 `pop` 表 | 上游資料源 |
| --- | --- | --- | --- | --- |
| `company_list` | `stk_code` | — | `raw_company_info`、`company_business_items` | TWSE/TPEx OpenAPI、經濟部 |
| `company_basic_info_list` | `stk_code`、`market`、`industry_code` | `listing_date` | （主題 seed 的母體）、`raw_dividend_history_yfinance` | 純 SQL；yfinance |
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
| `company_list`／`company_basic_info_list` | 50 | ≤ 20 | `company_list` 每列會打經濟部；`company_basic_info_list` 每列會打 yfinance 股利歷史 |

規則：

1. **先看現有**：先用 `select` 查 `pop` 已有的列，能不爬就不爬。
2. **dry run 再寫入**：看 `pending_matching` 和產生的 SQL，確認在預算內才以 `dry_run=false` 執行。
3. **分批**：`row_cnt` 不超過上表，`timeout_seconds=300`。逾時會整批 rollback，此時把 `row_cnt` 減半重試。
4. **爬完立刻驗可得性**：只看本次 insert 的列的 raw 可用率，不必等整體的 `pop_raw_quality`。
5. **當月的日 K 要重爬才會更新**：`ohlcv_monthly_list` 一列是一整個月，insert 當天就凍結（I021）。題目要用最近幾天的日 K 時，先用 `crawl_delete_rows` 刪掉當月那一列（先 dry run），再 `crawl_insert_filtered` 重爬，每檔 1 次 TWSE／TPEx 呼叫。
6. **FinMind 先查額度**：先看 `crawl_status(lookback_minutes=60)`。FinMind 系 seed 的 `rows_inserted` 合計超過 200 時，延後這題的 FinMind 補爬。
7. **不動排程**：除非 issue 的修復步驟需要，不呼叫 `crawl_set_schedule`／`crawl_pause`。

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
| L1／L2 | `app/` | `pytest tests/source/ tests/endpoint/ -q`，並新增重現本 issue 的測試 | 重啟 uvicorn | §10.2 重爬受影響的列 |
| L3 | `db/poc/*_list.sql` | `pytest tests/sql/ -q` | `poc_rebuild_views` → `pop_check_definitions` → `pop_update_mat_views(dry_run=true)` → 執行（seed 只替換 `hidden` view，資料保留） | 新邊界外的舊列：保留或刪除，在 issue 寫明理由 |
| L4／L5 | `db/poc/raw_*.sql`、`db/poc/<view>.sql` | `pytest tests/sql/ -q`；`RUN_POC_VIEW_SELECT_TESTS=1 pytest -m poc_view_select tests/live_sql/ -v` | 同 L3，但 IMMV 會 DROP 再重建，對所有既有 seed 列重打 API | 執行前先估重打成本（見下方注意事項） |
| L6 | 排程、`job_control.http_config` | — | `crawl_set_schedule`／`crawl_pause` | §10.2 重爬被凍結的列 |
| 已被修好的舊問題 | — | — | — | API 現在已回正確資料、只是舊列被凍結（例：§12 的借券 404）→ 直接 §10.2 重爬 |
| L7 | `questions/Q###.md` | — | — | — |

注意事項：

- **改 SQL 要對照 [`db/poc/README.md`](../db/poc/README.md) 的規則**：只要動到 `db/poc/*.sql`（含 `_list`、`raw_*`、正規化 view）或 `db/setting.sql`，在 issue 的「db/poc 規則檢查」表逐條寫出檢查結果（範例見 I017）。至少檢查：

  | 規則 | 檢查什麼 |
  | --- | --- |
  | 1、3 | 非 `_list` 不可有 mutable 操作（`::DATE`、`http_get`）；需要時封裝成 `db/setting.sql` 的 immutable function |
  | 2、9、11 | `_list` 不可呼叫 `http_get_content`（DAG 根節點例外）；`raw_*` 要有 `_list` 上游、不重複發查 |
  | 6、7 | 列唯一；主鍵取自 `_list` 的欄位（I004） |
  | 13 | `_list` 的邊界寫進產生器，不要「多產生再 WHERE」；起點對齊資料源實際覆蓋 |
  | 14、15、20、21 | 不同時間維度、事件 vs 連續、不同上游、多方消費的展開 view 各自分 `_list` |
  | 16 | 只用 INNER／CROSS JOIN（pg_ivm 不支援 OUTER JOIN）；也避免 window function、`DISTINCT`、`GROUP BY`、WHERE 內的 subquery、CTE（I018 的 `DISTINCT ON` 去重因此不可行） |
  | 17 | 事件 key 選全部非 NULL 的欄位 |
  | 18 | 欄位規格會變動時（增刪欄位、換主鍵、換 endpoint）新增 `<name>_v2.sql`，不要就地改寫；只修 bug、規格相容時才就地改 |
  | 22 | 包在函式裡的巢狀 JSON 路徑，第一步加括號 |

  再跑 `pytest tests/sql/ -q`（含 view 建立與 LineageX 欄位血緣），上線前用 `pop_check_definitions` 看 drift。同一份 `db/poc/README.md` 中該 SQL 的段落（設計理念、邊界）若因此不成立，要一起改。
- **IMMV 重建成本**：`pop_update_mat_views(dry_run=true)` 會列出 `seed_upstreams`。重打 API 次數約等於該 seed 的 `pop_rows`（扣掉 http_cache 命中）。FinMind 系超過 200 列時，先 `crawl_pause` 相關 FinMind 排程並分時段執行，否則 402 會把整批列凍結成 found=false。
- 修改涉及 SQL 通用規則時，同步更新 `db/poc/README.md`。

### 10.2 被凍結的壞資料要重爬

seed 列一旦 insert，下游 IMMV 的那一列就固定了，修好 API 後舊列也不會自己變好。重爬步驟：

1. **確認 API 已是修好的版本**：直接 `curl` 一個受影響的 URL，確認回應已正確。
2. **刪除壞列**：`crawl_delete_rows`，先 dry run，看 `matching_rows`、`pinned_responses`，以及每個下游 IMMV 的 `one_to_one`；確認後以 `dry_run=false` 執行，結果必須是 `deleted`。
3. **確認快取**：`crawl_delete_rows` 已清掉它 pin 過的 URL。若還有其他相關 URL 在一小時內被抓過（2xx、4xx 都會被 cache），用 `http_cache_evict` 先 dry run 再清。
4. **重爬**：`crawl_insert_filtered`（同 §8.3 預算）。
5. **重跑**：重跑 `found_by` 所列問題的斷言，結果寫回 issue，並記一行到 LEDGER 的「重爬紀錄」。

注意：

- 刪上游 seed（如 `company_list`）**不會**連動刪下游 seed 表（`company_basic_info_list` 是獨立的表）。
- 該 seed 若有 cron 排程，刪掉的列也可能被 cron 先爬回來（效果等同重爬，但不在你的預算控制內）。大量重爬前可先 `crawl_pause`，完成後再依 `crawl_status` 原本的值 `crawl_set_schedule`。

### 10.3 重爬工具與實測（2026-11-25，指令 0）

[`server.py`](../server.py) 新增了兩個寫入 tool，邏輯在 [`pop_maintenance.py`](../pop_maintenance.py)，測試在 [`tests/pipeline/test_pop_maintenance.py`](../tests/pipeline/test_pop_maintenance.py)。兩者都預設 `dry_run=true`。

| Tool | 行為 | 護欄 |
| --- | --- | --- |
| `crawl_delete_rows(table, filters, dry_run=true, pin_responses=true, allow_unverified=false)` | 在一個交易內：鎖住 seed → pin 回應 → `DELETE` → 驗證 IMMV → 移除 pin → commit | 必須是 seed table；`filters` 不可為空；單次上限 500 列；驗證失敗整批 rollback |
| `http_cache_evict(url_like, dry_run=true)` | `DELETE FROM job_control.http_cache WHERE url LIKE <url_like>` | `url_like` 必須含 `/api/` 與 4 位以上數字（股票代號或日期）；單次上限 5000 筆 |

**為什麼刪除這麼麻煩**：在真實 DB 實測（pg_ivm 1.17，每個實驗都在交易內 rollback）得到四個事實：

| 實驗 | 結果 |
| --- | --- |
| API 沒開時刪一列 | 失敗：`Failed to connect to host.docker.internal port 5002`。pg_ivm 會對被刪的列**重跑 IMMV 查詢，包含 API 呼叫** |
| 回應沒變的列（2330 2020-01-05） | 1 次 API 呼叫；raw 1 列、正規化 154 列都正確刪除 |
| 回應已變的列（2330 2015-08-05：當初 404，現在 200） | seed 列刪掉了，但 raw 列留著，沒有 pin 就會成為**孤兒** |
| 直接 `DELETE FROM pop.raw_sbl_history` | 被拒：`cannot change materialized view`，孤兒只能用整張 `refresh_immv` 清（每個 seed 列重打 API） |

重爬的情境幾乎都是「回應已變」，所以 `crawl_delete_rows` 會先 **pin**：把每個下游 `raw_*` 已存的回應依 URL 寫進 `http_cache`（NULL 寫成 404），讓 pg_ivm 重算出與當初相同的結果。URL 由 `db/poc/raw_*.sql` 把 `custom.http_get_content(<url>)` 改寫成 `<url>` 算出，22 個 raw view 都適用（有測試檢查）。驗證方式是：與 seed 1:1 的 IMMV（`raw_*` 與 `company_valuation` 這類一列對一列的表）必須剛好少掉被刪的列數；不符就 rollback，所以不會留下孤兒。

**1 列實測（真的 commit）**：`sbl_history_monthly_list {stk_code: "2330", month_start_date: "2015-08-05"}`

- 刪除：pin 1 筆、**0 次 API 呼叫**；`raw_sbl_history` 262 → 261 列（已驗證），`sbl_history` 沒有該月的列，不變；pin 已清除。
- 重爬：`crawl_insert_filtered` insert 1 列，0.04 秒。原本 NULL 的 raw 列變成可用，有 49 筆借券紀錄，`sbl_history` 也多了 49 列。

### 10.4 護欄

- 不 `DROP SCHEMA pop`、不 truncate seed 表；只刪 issue 中列舉的列。例外：使用者明確要求整個資料庫重建時，照 §10.6 先備份再重建。
- 所有寫入 tool 先 dry run，並把 dry run 的數字寫進 issue。
- 程式修改在 feature branch 上進行。跑完測試後回報 diff 與測試輸出，**等使用者確認再 commit**。
- 一個 issue 對應一個根因與一個修復範圍。修復時順手發現的其他問題另開 issue。
- 不為了讓斷言通過而放寬斷言（見 §1）。

### 10.5 上游失敗被凍結：`retry_failed.py`

大量爬取時上游常會中途失敗（TWSE 封鎖頁、FinMind 配額／封 IP、MOPS 逾時）。API 回 5xx 時 raw 存成 NULL；回 200 但上游某次呼叫失敗時，回應帶 `source_errors`。兩種都會被凍結在 pop，**不會自己重試**。

```bash
python verify_questions/retry_failed.py                       # dry run：每個 raw view 的失敗列數
python verify_questions/retry_failed.py raw_sbl_history --apply --batch 5 --pause 2
python verify_questions/retry_failed.py raw_monthly_revenue_twse --apply --include-not-found   # 修好「誤判無資料」的 adapter 之後
```

- 刪除走 `pop_maintenance.delete_seed_rows`（同 `crawl_delete_rows` 的 pin + 1:1 驗證），再從 `hidden.<seed>` 把同一批 key insert 回去。
- NULL 也可能是正當的 404（例：該年度沒有年報）。重試後仍是 NULL 的列，查 `job_control.http_log` 的 `status_code`：404 就是真的沒資料，不用再重試。
- 上游還在封鎖時重試只會再凍結一次：先 `curl` 確認上游恢復；FinMind 回 `ip banned` 時要等 `retry_after` 秒數過去。
- 每次大量爬取（指令 3、§10.6 重建）之後都跑一次 dry run，數字記進當次 run 紀錄。
- **只改了正規化 SQL**（raw 不變）時，不要讓 `update_mat_views` 重打上游：先 `urls = pop_maintenance.pin_seed_responses(conn, '<seed>')`，再 `Pipeline().update_mat_views(['<正規化表>'])`，最後 `pop_maintenance.unpin_responses(conn, urls)`。重建會逐字使用 raw 存的回應，零上游呼叫、兩表一致（I004 第二批實測 8 張、0 次 HTTP）。
- 刪除被 rollback（`pg_ivm recomputed a response that differs`）代表 raw 與正規化 IMMV 當初拿到不同回應（I011）。改成依序重建該 seed 的兩張 IMMV：先 `SELECT pgivm.refresh_immv('pop.raw_<x>', true)`，再 `pop.<x>`。會對該 seed 每列重打一次 API，但第二次全部命中 `job_control.http_cache`（TTL 1 小時），兩表必定一致。先 `curl` 確認上游正常；若 adapter 剛改過，先刪掉該 endpoint 在 `http_cache` 的舊快取。

### 10.6 整個資料庫重建（使用者要求時）

依 repo README 的步驟 (4)(8)：`Pipeline().create_views()` 重建 poc，`Pipeline().create_mat_views(recreate=True)` 重建 pop 與 hidden。重建會清掉所有 seed 列，先備份、重建後還原涵蓋範圍：

1. 備份：`pg_dump -n pop -n hidden -Fc` 與 seed 表 data-only dump 放 `data/backup/`（已 gitignore），並把每張 `pop.*_list` 複製到 DB 內的 `seed_backup_<日期>` schema（重建不會動到）。
2. 確認 API 是新版、上游可用（TWSE／FinMind 沒有封鎖）。快取中毒的磁碟快取先清掉（I010）。
3. 重建後 `pop_check_definitions` 應該沒有任何 drift。
4. 還原：依 seed 依賴順序，把 `hidden.<seed>` 中 key 與備份相同的列分批 insert（下游 IMMV 會用新版 API 重爬）；seed 的 key 規格有改時要對應（2026-11-25 例：月營收 1 日 → 10 日、季報季末 → 公告期限）。FinMind seed 放最後。
5. 跑 §10.5 的 `retry_failed.py`，把還原過程中的上游失敗重爬。
6. 重跑所有問題（指令 3 的斷言部分）與盤點（指令 1），記進 LEDGER。

2026-11-25 的實際重建紀錄見 LEDGER「重爬紀錄」與 I002／I010。

## 11. AI 指令

把下列文字貼給 Claude Code（在本專案目錄）。`<...>` 換成實際值。

### 指令 0：初始化（只做一次；2026-11-25 已完成，結果見 §10.3）

```text
依 verify_questions/README.md §3 建立資料夾骨架（LEDGER.md 依 §6.4、questions/ runs/ issues/ checks/ snapshots/），
再依 §10.3 在 server.py 新增 crawl_delete_rows 與 http_cache_evict 兩個預設 dry_run 的 MCP tool，並補上測試。
完成後用 1 列實測刪除 seed 列是否觸發 API 呼叫，把結果寫回 README §10.3。
回報 diff 與測試結果，不要 commit。
```

### 指令 1：盤點

```text
依 verify_questions/README.md §4 步驟 1 盤點資料庫：執行 .venv/bin/python verify_questions/snapshot.py
（crawl_pop_tables、pop_raw_quality、crawl_status(lookback_minutes=1440)、http_stats(minutes=1440) 加上 §7.5 維度剖析），
存成 snapshots/<今天>.json。與上一份 snapshot 及 LEDGER「剖析基線」比較，更新 LEDGER.md 的品質趨勢、維度矩陣與剖析基線；
基線裡還沒分類的欄位與實體重複，依 §7.5 分類。列出：usable% 下降的 raw、新的全空欄位或非空率下降的欄位、
新出現或變多的實體重複、最新資料日異常的表、維度矩陣的空格（準確、唯一優先）、可疑的新現象（各附一句證據）。
```

### 指令 2：出題

```text
依 verify_questions/README.md §5 出 <N> 題新問題卡（status: draft），格式照 §6.1。
優先填 LEDGER 維度矩陣的空格（準確、唯一優先），再來是沒被覆蓋的 pop 表與 §12 的候選 issue。
每題照 §5.2：斷言標題用 §7.1 的標籤、至少 3 個 DAMA 維度、至少一道錯值防線（先查 adapter，確認比對的兩邊獨立，§7.2）、
至少一個邊界案例，補爬量要在 §8.3 預算內。accuracy 的 golden 值從管線以外的權威來源取得，註解寫出處與取得日期。
門檻依 §7.2 寫出理由；新規則先對全表跑一次。每句 SQL 都先用 select 確認可執行。
寫完列出清單（標題、策略、覆蓋的表 × 維度、預估補爬列數）給我審，不要補爬。
```

### 指令 3：執行問題

```text
依 verify_questions/README.md §4 步驟 3–5 執行 <Q###>：
先 select 現有資料 → 依 §8 補爬（先 dry run，遵守 §8.1 依賴鏈與 §8.3 預算）→ 跑決策 SQL → 跑全部斷言。
寫 runs/<今天>_<Q###>.md（格式 §6.2），含維度結論：九個維度各是 PASS、FAIL 或未驗證。
決策結論會觸發動作時（例：列入觀察），對觸發結論的 1–2 個關鍵值做準確性抽查：查官網或公司公告，
算進 §8.3 預算，上游封鎖時記 INCONCLUSIVE。更新問題卡的 status、last_run、歷次結果，以及 LEDGER（含維度矩陣）。
斷言失敗依 §7.3 分流，需要時開 issue（格式 §6.3，填 dimension）。不要修改程式。
```

### 指令 4：診斷

```text
依 verify_questions/README.md §9 診斷 <I###>：
用 §9.1 的證據順序收集證據（http_log、http_cache、hidden、直接呼叫 API、對照 raw JSON 與正規化 SQL、必要時對照上游官網），
判定根因層級 L0–L7 與違反的 DAMA 維度（§7.1），把證據、根因、修復方案寫回 issue。修復方案要包含：改哪些檔、
是否需要 _v2 SQL（rule 18）、影響多少既有列、預估重打 API 次數；動到 db/poc SQL 時，依 §10.1 逐條對照 db/poc/README.md 的規則，
寫進 issue 的「db/poc 規則檢查」。再做同類掃描：把同一個失效模式寫成 SQL 掃所有 pop 表，
結果寫進 issue 的「同類掃描」；掃到不同根因的另開 issue。status 改為 diagnosed。只讀，不改程式，也不寫 DB。
```

### 指令 5：修復

```text
依 verify_questions/README.md §10 修復 <I###>（照 issue 裡已寫好的方案）：
開 feature branch 修改 → 動到 db/poc SQL 時，照 §10.1 對照 db/poc/README.md 的規則並更新 issue 的「db/poc 規則檢查」→
補上能重現本 issue 的測試 → 跑該層的必跑測試 → 上線（重啟 API 或重建 view／IMMV，先 dry run）→
依 §10.2 用 crawl_delete_rows 刪壞列（先 dry run）、必要時 http_cache_evict、crawl_insert_filtered 重爬 → 重跑 found_by 所列問題的斷言 →
確認復發時有東西會報警：某個問題卡的斷言或 §7.5 剖析抓得到這個失效模式，沒有就在 found_by 的問題卡補一個斷言。
每一步的實際數字寫回 issue；斷言全部通過才把 status 改為 closed。完成後執行指令 8（commit、push、確認 PR check 全過）。
```

### 指令 6：回歸

```text
依 verify_questions/README.md §4 步驟 7 回歸：對所有 status 為 passing 的問題只跑斷言（不補爬），
再跑 .venv/bin/python verify_questions/snapshot.py --no-save，對照 LEDGER「剖析基線」。
變成 FAIL 的問題改為 failing；剖析相對基線退化（新的全空欄位、非空率下降、新的實體重複）也算退化。
兩者都依 §7.3 開 issue 或附加到既有 issue。在 LEDGER 品質趨勢記一行。
```

### 指令 7：一輪完整循環

```text
依 verify_questions/README.md 跑一輪：
§2 前置條件 → 指令 1 → 挑一題（優先順序：failing > 最久未跑的 draft > 最久未跑的 passing；跳過的題見下方）執行指令 3 →
對新開的 issue 執行指令 4 → draft 少於 3 題時執行指令 2 補 3 題（優先填維度矩陣的空格）。補爬遵守 §8.3。
不要執行指令 5，把建議修復的 issue 依 §7.4 嚴重度排好（附維度）列給我。最後執行指令 8（commit、push、確認 PR check 全過）。
```

挑題時跳過：

- `failing` 但所有 FAIL 都是使用者決定保留的（寫在問題卡的已知限制，例：Q005 C1）。重跑只會得到同樣結果，視同 `passing`，由指令 6 回歸處理。
- 決策或斷言依賴一個還沒修好的 issue 的 draft（`issues` 欄有未 closed 的 issue，而且該 issue 讓題目一定 FAIL，例：Q007 依賴 I016）。等 issue 修好再跑，那次執行就是 issue 的驗證。
- 補爬會打到工作目錄有未 commit 改動的 endpoint 的題目（§2）。

跳過的題與理由寫進 LEDGER「最新盤點發現」。

### 指令 8：提交並確認 PR check 全過（每次改 pipeline 邏輯或解完一個 issue 都要做）

觸發時機：只要改了 data pipeline 邏輯（`app/`、`db/poc/*.sql`、`db/setting.sql`、`pipeline.py`、`pop_maintenance.py`、`server.py` 等），或解完一個 issue（指令 5 把 status 改為 closed），就立刻執行，不要累積多個改動才一起提交。

```text
依 verify_questions/README.md 指令 8 提交這次的改動：
1. 跑該層的必跑測試（至少 `pytest -q` 全綠）。這次改到 db/poc/*.sql 或 db/setting.sql 時，確認 issue 的「db/poc 規則檢查」
   已逐條填好（§10.1），而且 `pytest tests/sql/ -q` 全綠。
   只 stage 這次指令產生的檔案；工作目錄裡不屬於這次的改動（例如使用者進行中的 app/ 修改）不要一起 commit。
2. git commit（訊息以 `robot:` 開頭，寫明 issue 編號、根因、修法、實測數字），然後 git push 到目前的 feature branch。
3. 找出這個 branch 對 main 的 PR（`gh pr list --head <branch> --base main`）。
   沒有 PR 就開一個（base main），不要 merge。
4. 等這次 push 觸發的 CI 跑完（`gh pr checks <PR> --watch`），確認所有 check 都通過。
5. 有 check 失敗就補救：用 `gh run view <run-id> --log-failed` 找出原因。
   - 程式或測試造成的（包括測試裡寫死、已和新邏輯不一致的值）：修好 → 回到第 1 步，再 commit、push、等 CI。
   - 上游環境造成的（例如 TWSE／MOPS 對 CI runner 回 307 WAF 封鎖頁、`rate_limited=True`、FinMind 402）：
     附上 log 證據，用 `gh run rerun <run-id> --failed` 重跑一次；重跑仍失敗才當成程式問題處理。
   重複直到所有 check 都通過。
6. 把 commit hash、PR 連結、CI 結果（含補救過程）寫回對應的 issue 與 LEDGER。
   一個 commit 記不了自己的 CI 結果：留在工作目錄，下一輪指令 1 更新 LEDGER 時一起提交，不要為了記錄 CI 再開一個 commit。
```

注意：前一次 push 的 CI 還沒通過時，不要開始下一個 issue。每個 issue 都要在 check 全過的狀態下收尾，CI 失敗才找得到是哪一次改動造成的。

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
| `raw_sbl_history` usable 僅 40.5%（200／336 列 NULL） | NULL 全是 HTTP 404，集中在 2005-01 ～ 2015-08；2330 在 2015-09 之後 0 列 NULL。**2026-11-25 更新**：2330 2015-08-05 現在 API 回 200，重爬後有 49 筆（§10.3），剩 199 列 | 很可能是 2026-11-23 backend 回溯修正前被凍結的舊列，屬「已被修好的舊問題」，只需 §10.2 重爬。仍要抽查更早年份（2005–2014）是否也拿得到，拿不到的部分才是 L3／L0 |
| `raw_monthly_revenue_twse` 有 111 列 found=false | 全部是 as_of 2010-01 ～ 2013-01；2013-02 之後全部可用 | 同上：seed 邊界（L3）或 MOPS t21sc03 舊格式（IFRS 前）parser 不支援（L1） |
| `raw_company_info` 有 187 列 found=false（17.7%） | 這些公司進不了 `company_basic_info_list`，所有主題 seed 都不會有它們 | 需要確認是興櫃／下市（L0，已知限制）還是漏抓（L1） |
| `raw_finmind_news` usable 72.6%（26 列 NULL） | — | 待診斷（FinMind 限流 L6？） |
| `company_valuation` 有 12 列 data_date 落後 as_of 超過 7 天 | 全是 as_of 2022-02-05 → data_date 2022-01-26 | **不是資料問題**：春節休市（L7）。斷言門檻應為 12 天，如 Q001 C4 |
| 2454、1101 不在 `company_list` | `company_list` 目前 1,055／約 2,250 家 | 不是品質問題，是還沒爬到；執行問題時依 §8.1 由上往下補 |
| 2317 近 3 年估值只有 13／37 個月 | `pop.company_valuation` | 還沒爬到；Q001 第一次執行時補爬即可 |

已驗證通過（可作為回歸基準）：`ohlcv_daily` 的 OHLC 合理性與主鍵唯一性；估值收盤價 vs 日 K 收盤價（543 列可比對、0 違規）；FinMind vs yfinance 現金股利（26 列可比對、0 違規）。

### 12.1 DAMA 維度補盤（2026-11-26）

導入 §7.1 時，依九個維度對 live DB 補盤一次。以下是候選問題，要經指令 3／4 確認。2026-11-27 的指令 7 已轉成 issue：借券重複 → I017、新聞重複 → I018、2882 季度重複 → I019、2882 EPS 落差 → I020；必定成立的斷言已改寫 Q006 C2，Q001 C3、Q005 C3 待改。

| 維度 | 觀察 | 證據 | 初步判斷 |
| --- | --- | --- | --- |
| 準確性（斷言設計） | Q001–Q008 共 39 個斷言沒有一個 `accuracy`；6 個 `internal` 中有 3 個比的是 adapter 由對方算出來的欄位，必定成立：Q001 C3 的互推部分、Q005 C3、Q006 C2 | `app/valuation_source.py`：`eps`＝close ÷ PER 等（354–356 行）；`market_per`＝Σ市值 ÷ Σ純益（490–496 行） | L7：改寫成獨立比對。Q001 可改用 §6.1 範例的 C4；Q005 C3、Q006 C2 保留範圍與 NULL 檢查，刪掉互推部分 |
| 準確性 | 2882 的估值隱含 EPS 比 MOPS 近四季 EPS 合計低 6–7%，6／6 列都偏低。同一個比對在 2330 只有 3 月對不上（早公告年報，見 §6.1 已知限制），6488 0 違規 | as_of 2026-06-05 ～ 08-05：6.59 vs 7.03；09-05 ～ 11-05：8.48 vs 9.12 | 待診斷：TWSE 對金控的 EPS 基準不同，或 I009 修正後金融股的 MOPS EPS 有誤 |
| 唯一性 | `finmind_news` 同一 link 重複 321 組、多 431 列（全表 1,955 列的 22%），幾乎都在同一個 as_of 內 | 同一篇「不斷更新」文章收錄了多個標題版本；Yahoo股市與奇摩股市轉載同一 link | 新聞數類的事件決策會重複計算：L5 去重，或寫進已知限制 |
| 唯一性 | `sbl_history` 同一筆借券交易出現在兩個 as_of：290 組、多 302 列 | 例：2317 2025-03-27 議借 432 張，同時出現在 as_of 2025-09-05 與 2025-10-05 | 相鄰取樣窗口重疊，跨月加總會重複算。另有 445 列是同一 as_of 內條件完全相同的列，可能是真實的多筆交易，待確認 |
| 唯一性／及時性 | `mops_quarterly_financials` 的 2882 2026Q2 出現兩次（as_of 2026-08-31、2026-11-14） | as_of 11-14 那列應該是 Q3，卻回 Q2 | 金融業 Q3 還拿不到時回了上一季，並被凍結。待診斷是 L0（尚未公告）還是 L1（fallback） |
| 唯一性 | `company_business_items` 有 11 家公司的同一營業項目代碼重複，多 263 列 | seed `company_list` 沒有重複代號（1,057／1,057），重複來自上游清單 | L0；L5 加 DISTINCT，或寫進已知限制 |
| 完整性 | 13 個全空欄位：8 個是 I016 的 TTM 欄位，其餘 5 個待分類（`company_valuation.reason`、`financial_analysis.reason`、`dividend_yfinance` 的 3 個日期欄位） | `snapshot.py` 剖析 | 下一次指令 1 依 §7.5 分類 |

已驗證通過（新增的回歸基準）：收盤價升降單位、漲跌幅限制、月營收最新一期（§7.1 範例），以及估值隱含 EPS vs MOPS 近四季 EPS（§6.1 C4，2330 34 列）。
