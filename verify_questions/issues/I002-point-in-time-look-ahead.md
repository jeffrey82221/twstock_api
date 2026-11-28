---
id: I002
title: as_of 看得到尚未公布的月營收與季報（前視偏誤）
severity: S1
layer: L2／L3（設計）
status: closed
pop_tables: [monthly_revenue_twse, mops_quarterly_financials]
seeds: [financial_month_twse_list, mops_quarterly_financials_quarter_list]
found_by: [Q002]
---

## 症狀（附可重現的 SQL）

回測時「as_of 當天看得到的資料」不能包含當天尚未公布的數字。目前：

- `monthly_revenue_twse`：as_of = 每月 1 日，卻已有上個月營收（法定期限次月 10 日）。[Q002](../questions/Q002-growth-revenue-momentum.md) C3：36／36 列違規。
- `mops_quarterly_financials`：as_of = 季末日，卻已有當季財報（期限 Q1–Q3 季末後 45 天、Q4 90 天）。[Q002](../questions/Q002-growth-revenue-momentum.md) C4：73／73 列違規。

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

## 使用者決定（2026-11-25）

「選對未來分析與程式架構最好的，不在意重爬」→ 方案 B（API 的 as_of = 當時可得）＋ 每列帶 `available_date`。理由：PIT 正確性由 API 保證，任何下游 view／回測查詢都不可能誤用；`available_date` 讓分析者看得到延遲。

## 修復紀錄

（2026-11-25，使用者決定後）採**方案 B（API 語意 + seed 重新取樣）**，加上方案 A 的 `available_date` 欄位：
- 新模組 [`app/pit.py`](../../app/pit.py)：法定公告期限規則集中一處（月營收次月 10 日；季報 Q1 5/15、Q2 8/14（金融業 8/31）、Q3 11/14、Q4 隔年 3/31）。
- `as_of` 一律改為「當天已公開的資訊」：
  - `/quarterly-financials`：`_period_for` 改用 `pit.latest_available_quarter`；金融業（不在 t163sb06）Q2 等到 8/31；回應加 `available_date`。`QUARTERLY_MIN_DATE` 2013-03-31 → 2013-05-15（第一季的公告期限），季度下限另存 `QUARTERLY_MIN_PERIOD`。
  - `/revenue/twse` 與 `/revenue`（FinMind）：只取 `revenue_available_date <= as_of` 的月份；回應加 `data_date`（月底）與 `available_date`。
  - `/financials` 等 TTM（`service._is_quarter_before`）：季度須已過公告期限（公司別未知，用金融業的保守期限）。
- seed 改以公告期限日取樣，每列恰好對應一個期間：`financial_month_twse_list.as_of_date` = 每月 10 日；`mops_quarterly_financials_quarter_list` 新增 `as_of_date`（5/15、8/31、11/14、隔年 3/31，未到期限的季度不產生）。
- 正規化 view 新增欄位：`monthly_revenue_twse.data_date / available_date`、`mops_quarterly_financials.available_date`。
- 因為使用者要求整個資料庫重建（poc／pop／hidden），規格變更直接改原檔，不另開 `_v2`（rule 18 保護的 pop 資料已備份並重爬）。
- 測試：`tests/source/test_pit.py`（新）、`test_mops_quarterly_financials_source.py`（PIT 3 個新案例；既有案例的 as_of 改到公告期限之後）、`test_revenue_twse_source.py`（2 個）。

## 重爬與驗證

（2026-11-26，整個資料庫重建後）
- 使用者要求重建 poc／pop／hidden（README 步驟 (4)(8)），所以 seed 依新規格重新產生、舊列依 key 對應還原（月營收 1 日 → 同月 10 日、季報依 `quarter_end`）。
- `mops_quarterly_financials`：356 列、**0 列 `available_date > as_of`**，每列 `data_date` = seed 的 `quarter_end`（1 列 8463 2013-Q1 為上市前的正當 404）。
- `monthly_revenue_twse`：484 列、usable 100%。
- 直接呼叫：2330 `as_of=2026-11-01` → 2026/09（`available_date` 2026-10-10）；`as_of=2026-11-10` → 2026/10；2330 `as_of=2024-05-10` → 2023 Q4；2882 `as_of=2025-08-20` → 2025 Q1（金融業 Q2 要 8/31）。
- [Q002](../questions/Q002-growth-revenue-momentum.md)：**6／6 PASS**（C3、C4 改為獨立重算期限並交叉檢查 `available_date`）。
