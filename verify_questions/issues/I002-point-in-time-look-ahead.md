---
id: I002
title: as_of 看得到尚未公布的月營收與季報（前視偏誤）
severity: S1
layer: L2／L3（設計）
status: diagnosed
pop_tables: [monthly_revenue_twse, mops_quarterly_financials]
seeds: [financial_month_twse_list, mops_quarterly_financials_quarter_list]
found_by: [Q002]
---

## 症狀（附可重現的 SQL）

回測時「as_of 當天看得到的資料」不能包含當天尚未公布的數字。目前：

- `monthly_revenue_twse`：as_of = 每月 1 日，卻已有上個月營收（法定期限次月 10 日）。Q002 C3：36／36 列違規。
- `mops_quarterly_financials`：as_of = 季末日，卻已有當季財報（期限 Q1–Q3 季末後 45 天、Q4 90 天）。Q002 C4：73／73 列違規。

```sql
SELECT stk_code, as_of, latest_month_label FROM pop.monthly_revenue_twse
WHERE stk_code = '2330' AND as_of >= DATE '2026-09-01' ORDER BY as_of
```

## 證據
- `/api/company/{id}/quarterly-financials` 說明：「選取不晚於該日的最近**完整季度**」——以季度結束日判斷，不是公布日。seed `mops_quarterly_financials_quarter_list` 的 as_of 就是季末日，所以每列都拿到「當天尚未公布」的當季。
- `/api/company/{id}/revenue/twse`：as_of = 每月 1 日時回上個月營收（2330 as_of 2026-11-01 → 2026/10；法定期限 11/10）。
- 兩個 endpoint 的 as_of 語意都是「資料期間」，不是「當時可得」。

## 根因

as_of 被當成「資料期間的基準日」使用，資料庫沒有任何欄位記錄「何時可得」。對即時分析沒有影響，但用於回測時會產生前視偏誤。這是設計層級的問題（API 的 as_of 語意 + seed 的取樣日），不是單一 bug。

## 修復方案

需要使用者決定方向（status 維持 diagnosed，不進指令 5）：

- **方案 A（建議，最小變更）**：在正規化層加 `available_date` 欄位（月營收 = 次月 10 日、季報 = 季末 + 45／90 天，純 SQL 由 `data_date` 推得），回測一律以 `available_date <= 決策日` 過濾。rule 18：新增欄位屬規格變更 → 新增 `monthly_revenue_twse_v2`／`mops_quarterly_financials_v2`，或放在將來的分析層 plain view（不需重爬）。
- **方案 B**：API 的 as_of 改為「當時可得」語意（以法定期限或實際公告日判斷），seed 改以公布期限日取樣。改動 API 語意 + seed 主鍵 → 必須全部 `_v2` 並重爬。
- 方案 A 用法定最晚期限，對提早公布的公司偏保守（不會前視，但會晚幾天看到）。

## 修復紀錄

## 重爬與驗證
