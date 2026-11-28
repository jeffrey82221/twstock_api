---
id: I021
title: 日 K 當月那一列在 insert 當天就凍結，之後的交易日永遠不會進來
severity: S3
dimension: 及時性（另含完整性：該月月底前的交易日缺）
layer: L3（seed 產生未結束的月份）＋ 流程
status: diagnosed
pop_tables: [raw_ohlcv_monthly, ohlcv_daily]
seeds: [ohlcv_monthly_list]
found_by: [Q011]
---

## 症狀（附可重現的 SQL）

台北時間 2026-11-27，所有有 11 月日 K 的股票（5 檔）最新一筆都是 2026-11-25，缺 11-26。

```sql
SELECT stk_code, max(trade_date) FROM pop.ohlcv_daily WHERE trade_date >= DATE '2026-11-01' GROUP BY 1
```

[Q011](../questions/Q011-technical-ma60-volatility.md) C6（freshness）3／3 違規。

## 證據

- `db/poc/ohlcv_monthly_list.sql` 用 `generate_series(..., CURRENT_DATE, INTERVAL '1 month')`，所以當月的 `(stk_code, 2026-11-01)` 會進母體；`raw_ohlcv_monthly` 對它打一次 `/api/ohlcv?from=月初&to=月底`，回應裡只有「當天以前」的交易日。
- seed 列 insert 後 IMMV 就固定（README §1「修復要閉環」），11 月這一列不會再更新。`job_control.http_log`：`from=2026-11-01` 的請求在 11-23 ～ 11-25 之間，之後沒有。
- 近 30 天的 http_log 裡，只有當月（2026-11）是在月底前抓的；過去的月份都是月底後才 insert，所以完整。這個問題只影響「抓的時候還沒結束的那個月」。
- 其他每月取樣的 seed（估值、法人、外資持股、月營收、借券）都是「取樣日當天的快照」，不是「一段期間」，不受影響。

## 根因

L3：母體包含尚未結束的期間，而期間型的回應在 insert 當下就凍結。流程上也沒有「當月要重爬」的步驟。

## 同類掃描

期間型（一列 = 一段期間）的 seed 只有 `ohlcv_monthly_list`。`finmind_news_monthly_list` 是單日；`sbl_history` 是「as_of 之前 31 天」，as_of 已過才 insert，不受影響。

## 修復方案（待使用者決定）

- **A（流程，不改程式）**：需要最新日 K 的題目（例：[Q011](../questions/Q011-technical-ma60-volatility.md)），在指令 3 補爬時先用 `crawl_delete_rows` 刪掉當月那一列，再 `crawl_insert_filtered` 重爬（每檔 1 次 TWSE／TPEx）。寫進 README §8.3。
- **B（L3）**：seed 只產生已結束的月份，當月改由另一個「當月日 K」seed 處理（rule 14、rule 20：不同更新節奏分 `_list`）。改 SQL 時依 §10.1 對照 db/poc/README.md 規則。
- 建議先用 A；[Q011](../questions/Q011-technical-ma60-volatility.md) C6 是回歸防線。
