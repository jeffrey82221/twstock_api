---
id: I026
title: yfinance 上櫃股利有錯值與缺事件（5483 2023-01-12 記成 5.57、實際 3.20；3264 2023-06-20 的 3.50 元整筆沒有）
severity: S1                 # 錯值看起來正常（5.57 是乾淨的兩位小數），用 yfinance 算殖利率或配息總額會直接算錯
dimension: 準確性
layer: L0
status: diagnosed
pop_tables: [raw_dividend_history_yfinance, dividend_event_yfinance_list, raw_dividend_yfinance, dividend_yfinance]
seeds: [company_basic_info_list, dividend_event_yfinance_list]
found_by: [Q029]
---

## 症狀（附可重現的 SQL）

[Q029](../questions/Q029-otc-dividend-two-sources.md) C1、C2 FAIL。2023 年起兩張股利表（10 檔、61 個 FinMind 現金事件）只有 4 組不一致，全部在 [Q029](../questions/Q029-otc-dividend-two-sources.md) 的上櫃股：

```sql
WITH f AS (SELECT DISTINCT stk_code, cash_ex_dividend_date d, cash_dividend c FROM pop.dividend WHERE cash_dividend > 0 AND reference_date >= '2023-01-01'),
     y AS (SELECT DISTINCT stk_code, reference_date d, cash_dividend c FROM pop.dividend_yfinance
           WHERE cash_dividend > 0 AND reference_date >= '2023-01-01' AND stk_code IN (SELECT stk_code FROM pop.dividend))
SELECT coalesce(f.stk_code, y.stk_code) stk, coalesce(f.d, y.d) d, f.c finmind, y.c yfinance
FROM f FULL JOIN y ON y.stk_code = f.stk_code AND y.d = f.d
WHERE f.d IS NULL OR y.d IS NULL OR abs(f.c - y.c) > 0.00001 ORDER BY 1, 2
```

| 代號 | 日期 | FinMind | yfinance | 判定 |
| --- | --- | ---: | ---: | --- |
| 3264 | 2023-06-20 | 3.50 | （沒有） | **yfinance 缺事件** |
| 5483 | 2023-01-12 | 3.20 | 5.57 | **yfinance 錯值** |
| 5483 | 2024-07-25／07-26 | 5.30 | 5.30 | 合法：凱米颱風休市，TPEx 公告除息日 7/25、yfinance 記第一個交易日 7/26（[Q029](../questions/Q029-otc-dividend-two-sources.md) C1 已容許） |

## 證據

1. **TPEx 除權除息計算結果表**（`www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=…&endDate=…`，2026-11-27 20:25，管線沒有用到的 endpoint）：
   - 5483 112/01/12：除息前收盤 161.50、參考價 158.30、息值 **3.200000**、現金股利 3.20000000。
   - 3264 112/06/20：除息前收盤 65.50、參考價 62.00、息值 **3.500000**。
   - 5483 113/07/25：息值 5.300000（7/24–25 颱風休市）。
2. **直接呼叫 yfinance**（`yf.Ticker("5483.TWO").dividends`、`.splits`，2026-11-27 20:30）：2023-01-12 回 **5.57**；5483 的 split 只到 2011-08-05，所以 adapter 的還原倍數（`app/yfinance_source.py` `split_unadjust_factors`）是 1.0，5.57 是 Yahoo 原值。`yf.Ticker("3264.TWO").dividends` 在 2022-06-16（2.8）與 2024-06-28（3.3）之間沒有任何事件。
3. `pop.raw_dividend_history_yfinance` 5483 的 events 也是 5.57（adapter 照實存下）。

## 根因

L0：Yahoo Finance 的台股（上櫃）股利資料本身有錯值與缺漏。adapter 與正規化 SQL 都照實轉存，沒有放大錯誤。[I003](I003-yfinance-dividend-adjusted.md) 是另一個 L0＋L1 問題（配股還原），這裡沒有 split，不是同一個根因。

## 同類掃描

| 範圍 | 結果 |
| --- | --- |
| 2023 年起 FinMind vs yfinance（10 檔、61 個現金事件） | 只有上表 2 筆錯（另 1 組颱風日期合法） |
| 2023 年以前 | 不可比：FinMind 的 `dividend_event_list` 只爬到各股窗口內；已知的 2330 2007／2008、2882 2021／2022 金額差是 [I003](I003-yfinance-dividend-adjusted.md) 殘留（[Q005](../questions/Q005-income-dividend-consistency.md) C1 保留 FAIL） |
| 上市股 | 2023 年起 0 筆錯（2330、2317、2882、1101、2454、3045，[Q014](../questions/Q014-cash-dividend-accuracy.md) 也確認 27 個事件對 TWSE 全部一致） |

錯的兩筆都是上櫃股；樣本只有 3 檔上櫃股（5483、3105、3264）+ 6488，無法斷言上櫃比較容易錯，但上櫃的 Yahoo 資料至少沒有被 [Q014](../questions/Q014-cash-dividend-accuracy.md) 這種權威比對驗過。

## 修復方案

L0 無法修上游。選項：

- **A. 下游以 FinMind 為主、yfinance 為輔**：README／`db/poc/README.md` 寫明 `dividend_yfinance` 只作交叉比對，殖利率與配息總額用 `dividend`。0 次 API 呼叫。
- **B. 新增權威來源**：TWSE `TWT49U` 與 TPEx `exDailyQ` 都公布每次除權息的息值、權值、參考價（[Q014](../questions/Q014-cash-dividend-accuracy.md)、本 issue 已用過），可以做成新的 `ex_dividend_result` adapter＋seed（類似 [I005](I005-otc-valuation-missing.md) 新增 TPEx 估值）。要新增 endpoint、`_list`、raw、正規化 SQL 與測試，依 §10.1 rule 14／20（不同上游各自分 `_list`）。
- **C. 只寫進已知限制**。

建議 A 立即採用、B 排進後續（也能當 [Q014](../questions/Q014-cash-dividend-accuracy.md) 類 accuracy 斷言的常設 golden）。由使用者決定。

- **影響的既有列**：`dividend_yfinance` 2 列（A、C 不動資料；B 不改 yfinance 的列）。
- **復發報警**：[Q029](../questions/Q029-otc-dividend-two-sources.md) C1、C2（上櫃）、[Q005](../questions/Q005-income-dividend-consistency.md) C1（上市）、[Q014](../questions/Q014-cash-dividend-accuracy.md) C2（對 TWSE）。

## db/poc 規則檢查

A、C 不動 SQL；選 B 時指令 5 依 §10.1 填寫。

## 修復紀錄

## 重爬與驗證
