---
id: I016
title: yfinance 季／年財報的 TTM 欄位全部是 NULL——Yahoo 只保留最近約 5 季，歷史 as_of 的列被凍結成「found=true 但全空」
severity: S2
layer: L0 + L3（seed 設計）+ L1（found 語意）
status: diagnosed
pop_tables: [raw_quarterly_financials_yfinance, financial_quarterly_yfinance, raw_yearly_financials_yfinance, financial_yearly_yfinance]
seeds: [financial_year_yfinance_list, financial_quarter_yfinance_list]
found_by: [Q007]
---

## 症狀（附可重現的 SQL）

兩張 yfinance 財報表合計 1,331 列，TTM 相關欄位**全部**是 NULL；季表有一半的列連最新一季都沒有。`pop_raw_quality` 卻把這些列算成 100% 可用（回應是 `found=true`）。

```sql
SELECT t, count(*) AS n, count(eps_ttm) AS eps_ttm_nn, count(revenue_ttm) AS rev_ttm_nn,
       count(net_income_ttm) AS ni_ttm_nn, count(operating_margin_pct) AS opm_nn, count(latest_quarter_eps) AS lq_eps_nn
FROM (SELECT 'q' AS t, * FROM pop.financial_quarterly_yfinance
      UNION ALL SELECT 'y', * FROM pop.financial_yearly_yfinance) x
GROUP BY t
```

| 表 | 列數 | eps_ttm | revenue_ttm | net_income_ttm | operating_margin_pct | latest_quarter_eps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 季（as_of 2025-03-31 ～ 2025-09-30） | 562 | 0 | 0 | 0 | 0 | 275 |
| 年（as_of 全部 2026-01-01） | 769 | 0 | 0 | 0 | 0 | 767 |

[Q007](../questions/Q007-quarterly-eps-yfinance-vs-mops.md) C2 對 5 檔的 8 列全部違規。

## 證據

直接呼叫 API（2026-11-26）：

| 請求 | 回應 |
| --- | --- |
| `/api/company/2330/financials/yfinance?as_of=2025-09-30` | `found: true`，`ttm_quarters: []`，所有數值 null |
| `…?as_of=2026-01-01` | `found: true`，`ttm_quarters: ["2025-09-30"]`，`latest_quarter_value: 17.44`，`ttm: null` |
| `…?as_of=2026-11-26` | `ttm_quarters: [2026-09-30, 06-30, 03-31, 2025-12-31]`，`ttm: 98.26`，營益率、營收 TTM 都有值 |

- Yahoo 的 `Ticker.quarterly_financials` 目前只有 2025-09-30 ～ 2026-09-30 共 5 季。TTM 需要 as_of 之前連續 4 季，所以只有 as_of 落在最近約兩季的公告期限之後，才算得出來。
- MOPS 對同一季的 EPS 和 yfinance 一致（[Q007](../questions/Q007-quarterly-eps-yfinance-vs-mops.md) C3：4 組可比對，0 違規），所以有值的部分是對的，問題只在「沒有值」。

seed 設計（`db/poc/`）：

- `financial_year_yfinance_list`：每家公司從 `trunc_year(CURRENT_DATE)` 起一年一列，所以 as_of 是當年 1 月 1 日（2026-01-01）。那天往前只有 1 季在 Yahoo 窗口內。
- `financial_quarter_yfinance_list`：由年表回應的 `ttm_quarters` 攤出季末日，as_of = 季末日。爬的當時 Yahoo 窗口較早，產生了 2025-03-31 ～ 2025-09-30；季末日當天該季本身尚未公告，TTM 又需要更早的 4 季。
- seed 列一旦寫入，IMMV 不會重抓。Yahoo 窗口往前移以後，這些 as_of 再也拿不到資料，但列已凍結成 `found=true` 的空結果。

API 語意（`app/yfinance_source.py`）：Yahoo 沒有 as_of 之前的季度時，仍回 `found: true`，也不記 source error。所以品質盤點把它算成可用，`retry_failed.py` 也不會挑它重試。

## 根因

- **L0**：Yahoo 只保留最近約 5 季的季報，無法回答歷史 as_of。
- **L3**：兩個 seed 產生的 as_of（當年 1/1、季末日）不是「Yahoo 窗口內、且 4 季都已公告」的日期；列寫入後凍結，窗口移動後無法補救。
- **L1**：沒有任何季度可用時回 `found: true`，把「拿不到」偽裝成「拿到了空資料」（和 [I010](I010-t86-failed-day-cached-as-empty.md)、[I014](I014-empty-answers-treated-as-failures.md) 同一類語意問題）。

## 修復方案（待使用者決定）

| 方案 | 做法 | 影響 | 成本 |
| --- | --- | --- | --- |
| A. 改 seed，只產生「爬得到」的 as_of（建議） | 季 seed 改為「每季公告期限」（`app/pit.py`），只保留 Yahoo 窗口內、且期限已過的季度（約最近 2 季）；隨時間往前滾動，每季期限一過就由 cron 爬一次，爬的當下就是 PIT 正確的快照，之後凍結也沒關係。年表 seed 同理改用期限日或直接停用 | rule 18：欄位不變但值全換 → 新增 `_v2` 或刪除既有 1,331 列後重爬 | 約 870 家 × 2 季 ≈ 1,740 次 yfinance 呼叫（寬鬆） |
| B. 修 API 的 found 語意（與 A 搭配） | 窗口內沒有 as_of 之前的季度，或湊不滿 4 季時回 `found: false`（`reason: outside_yahoo_window`），TTM 欄位才會被品質盤點看見 | 既有列的 `found` 會改變 → 需要重爬或刪除 | 只影響錯誤路徑 |
| C. 停用 yfinance 財報表 | 歷史季資料全部改用 MOPS（`mops_quarterly_financials` 已是 PIT、2013 起完整，含營收、營益率、EPS） | 刪除 4 個 view／IMMV 與 2 個 seed；下游若有人用 `eps_ttm` 要改讀 MOPS | 0 次呼叫 |

建議 **A＋B**：保留 yfinance 當 MOPS 的即時交叉驗證來源（[Q007](../questions/Q007-quarterly-eps-yfinance-vs-mops.md) C3），但只在它答得出來的日期發查。若不需要 yfinance 的營益率／TTM，**C** 最簡單。

不論選哪個，既有 1,331 列都要處理：A／B 要刪除重爬，C 直接刪除。
