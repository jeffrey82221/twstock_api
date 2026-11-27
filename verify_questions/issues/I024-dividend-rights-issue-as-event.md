---
id: I024
title: FinMind 股利把增資／減資除權（金額全 0）當成股利事件：dividend 已有 1 列，股利歷史裡還有 331 個（6.1%）等著被爬
severity: S3                 # 金額是 0，加總類決策不受影響；「配息次數／連續配息年數」會多算。另有 S4：331 次必定作廢的 FinMind 呼叫
dimension: 有效性
layer: L1（＋L0：FinMind 把除權事件放在股利資料集）
status: diagnosed
pop_tables: [raw_dividend_history, dividend_event_list, raw_dividend, dividend]
seeds: [company_finmind_list, dividend_event_list]
found_by: [Q014]
---

## 症狀（附可重現的 SQL）

Q014 C5（至少一項股利 > 0）出題試跑時 1 列違規；對全表跑也只有這 1 列（`dividend` 60 列）：

```sql
SELECT stk_code, reference_date, cash_dividend, stock_dividend, cash_ex_dividend_date, stock_ex_dividend_date, dividend_year
FROM pop.dividend WHERE coalesce(cash_dividend, 0) = 0 AND coalesce(stock_dividend, 0) = 0
```

→ 2882 2022-12-05：現金 0、股票 0、只有股票除權日、`dividend_year` = 「不適用」。

## 證據

- raw（`pop.raw_dividend` 2882 as_of 2022-12-05）：`{"year": "不適用", "cash_dividend": 0.0, "stock_dividend": 0.0, "stock_ex_dividend_date": "2022-12-05", "announcement_date": "2022-11-28", ...}`，`source: FinMind v4 TaiwanStockDividend`。
- TWSE 除權除息計算結果表（`www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=20221201&endDate=20221209`，2026-11-27 18:50）：2882 111/12/05 除權前收盤 44.00、參考價 43.16、權值 0.833205、類別「權」。國泰金 2022 年底辦現金增資，這是增資的除權，不是股利。
- yfinance 那條（`dividend_yfinance`）沒有這筆，因為 Yahoo 的 dividends 只有配息。

## 根因

- L0：FinMind 的 `TaiwanStockDividend` 收錄了現金增資除權（金額欄全 0、`year` 為「不適用」、只有 `StockExDividendTradingDate`）。
- L1：[`app/service.py`](../../app/service.py) `_dividend_row_to_event`（857 行）只要有現金或股票除權息日就當成事件，沒有檢查金額。這筆經 `raw_dividend_history` 變成 `dividend_event_list` 的一列，再被 `raw_dividend`／`dividend` 抓成一個「股利事件」。

影響：金額為 0，加總、殖利率類決策不受影響；但「配了幾次」「連續幾年配股配息」會多算一次，`stock_ex_dividend_date` 有值也會讓人誤以為有配股。

## 同類掃描

| 範圍 | 結果 |
| --- | --- |
| `dividend` 全表金額全 0 的事件 | 1／60（上表 SQL） |
| `dividend_yfinance` 全表金額全 0 | 0（yfinance 只有配息） |
| `raw_dividend_history`（423 檔、5,414 個事件）裡金額全 0 的事件 | **331 個（6.1%）**：`year` 為「不適用」139 個、有年度標籤（例：1441「100年」）192 個 |
| 抽查有年度標籤的兩個 | TWSE TWT49U 2011-09-15：1441 大東「權」0.501166、1464 得力「權」−0.164495（負值＝減資類），都是除權不是股利 |
| `hidden.dividend_event_list` 還沒爬的事件 | 5,354 個（`dividend` 目前只有 4 檔、60 列）；331 個全 0 事件都在裡面，cron 或補爬輪到時每個打一次 FinMind（402 風險，§8.3），得到一個全 0 的「股利」 |

`SELECT (e->>'year' = '不適用') AS na, count(*) FROM pop.raw_dividend_history, jsonb_array_elements(dividend_history->'events') e WHERE coalesce((e->>'cash_dividend')::numeric, 0) = 0 AND coalesce((e->>'stock_dividend')::numeric, 0) = 0 GROUP BY 1`

## 修復方案

- **改哪些檔**：`app/service.py` `_dividend_row_to_event`：現金與股票股利合計都是 0 時回 `None`（不需要依賴「不適用」字串）。FinMind 與 yfinance 共用這個函式，yfinance 沒有全 0 的事件，不受影響。
- **測試**：`tests/source/` 或 endpoint 測試加一個全 0、year「不適用」的 FinMind row，斷言 `/dividend/history` 不回這個事件。
- **_v2 SQL**：不需要。
- **影響的既有列**：`dividend_event_list` 1 列（2882 2022-12-05）。`hidden.dividend_event_list` 是從 `raw_dividend_history` **已存的回應**攤平的，只改 adapter 不會讓 331 個全 0 事件消失。兩個做法：
  - A. 只改 L1，再重爬 `raw_dividend_history`（`company_finmind_list` 423 列 = 423 次 FinMind，要分時段，§10.1 IMMV 成本）。
  - B. 另在 `db/poc/dividend_event_list.sql` 加條件排除金額全 0 的事件（L3；需要依 §10.1 對照 rule 13「邊界寫進產生器」與 rule 16），0 次上游呼叫。建議 B＋A 的 L1 修正（新資料不再進來），由使用者決定。
  - 兩者都要用 `crawl_delete_rows` 刪掉 pop 的 2882 2022-12-05（pin，0 次上游呼叫）。
- **重打 API 次數**：B 為 0；A 為 423 次 FinMind。
- **復發報警**：Q014 C5。

## db/poc 規則檢查

方案 A 不動 SQL；選方案 B 時，指令 5 依 §10.1 逐條填寫（重點：rule 13 邊界寫進產生器、rule 16 不用 WHERE subquery、rule 18 是否需要 _v2）。

## 修復紀錄

## 重爬與驗證
