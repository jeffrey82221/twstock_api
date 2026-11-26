---
id: I012
title: FinMind 股利以除息日查詢時拿到「上一次」股利（end_date 截掉當次事件）
severity: S2
layer: L1（adapter：query_dividend 取數窗口）
status: closed
pop_tables: [raw_dividend, dividend]
seeds: [dividend_event_list]
found_by: [2026-11-26 I011 調查 raw_dividend 時發現]
---

## 症狀（附可重現的 SQL）

`raw_dividend` 以每個股利事件的 reference_date 當 as_of 查詢，回應卻是前一次事件；每家公司最新一次股利永遠不會進 `dividend`。

```sql
SELECT stk_code, as_of, reference_date, dividend_year
FROM pop.dividend
WHERE reference_date IS DISTINCT FROM as_of
```

修復前：例 2317 as_of 2023-07-04 → 110 年股利（reference 2022-07-04）；as_of 2022-07-04 → NULL。2317 的 114 年（2026-07-02）股利不在表中。

## 證據

- FinMind TaiwanStockDividend 以 `date` 欄過濾 start/end；`date` 是**除權息基準日**，晚於除息日約 5 天（2317 110 年：除息 2022-07-04、`date` 2022-07-10）。
- `query_dividend` 用 `end_date = as_of` → 除息日當天查不到該次事件。`_pick_dividend` 本身用 `ref <= as_of`，沒有問題。
- `/dividend?as_of=2022-07-20` 正確回 110 年；`/dividend/yfinance` 沒有這個問題（yfinance 以除息日為索引）。
- Q005 C1（FinMind 與 yfinance 同一基準日比對）只比對得到錯位後剛好重疊的 32 組，沒有直接暴露。

## 根因

L1：adapter 把 as_of 直接當上游的 end_date，但上游過濾欄位（基準日）不是我們判斷可得性的欄位（除息日）。

## 修復紀錄

- `app/service.py`：`query_dividend` 的 end_date 改為 `as_of + FINMIND_DIVIDEND_RECORD_LAG_DAYS`（31 天）；仍由 `_pick_dividend` 只留除息日 ≤ as_of。公告日早於除息日，所以不會前視。
- `app/main.py`：endpoint 說明更新。
- `tests/source/test_finmind_dividend.py`：除息日當天回當次事件、前一天回上一次事件。

## 重爬與驗證

2026-11-26：清掉 `job_control.http_cache` 中 41 筆 `/dividend?as_of=` 快取，`pgivm.refresh_immv('pop.raw_dividend')` 再 `pop.dividend`（51 列，FinMind 51 次呼叫，正規化那次全部命中快取，兩表一致）。

- `raw_dividend` 可用 33／51 → 51／51；`reference_date = as_of` 0 → 51／51；`dividend` 無 NULL 現金股利。
- Q005 C1 可比對組數 32 → 50，違規仍 3（皆為 I003 的 yfinance 還原值，依使用者決定保留 FAIL）。
- 同時解決 I011 的 FinMind 股利 19 列分歧。
