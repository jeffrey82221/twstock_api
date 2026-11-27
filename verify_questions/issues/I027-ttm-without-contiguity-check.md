---
id: I027
title: TTM 取「最近 N 個有資料的期」而不檢查是否連續，上游缺一期時 TTM 會跨 N＋1 期（FinMind 季財報 2 列、I023 的月營收也是這個機制）
severity: S1                 # 值看起來正常；目前實際受影響的只有 2001-01-01 的 2 列，但機制在三個 TTM 計算都有
dimension: 一致性
layer: L1（＋L0：上游缺期）
status: diagnosed
pop_tables: [raw_yearly_financials, raw_monthly_revenue, raw_monthly_revenue_twse, monthly_revenue_twse]
seeds: [financial_year_list, financial_month_list, financial_month_twse_list]
found_by: [Q035]
---

## 症狀（附可重現的 SQL）

出 Q035 時掃 `raw_yearly_financials`（FinMind 年度財報）的 `eps.ttm_quarters`：76 列有 4 季，其中 2 列的 4 季不連續（第 1 季與第 4 季相隔 366 天，正常是 273–276 天）：

```sql
WITH r AS (
  SELECT stk_code, as_of, ARRAY(SELECT jsonb_array_elements_text(financials->'eps'->'ttm_quarters'))::date[] AS q,
         financials->'eps'->>'ttm' AS ttm
  FROM pop.raw_yearly_financials WHERE jsonb_array_length(coalesce(financials->'eps'->'ttm_quarters', '[]'::jsonb)) = 4
)
SELECT stk_code, as_of, ttm, q, q[1] - q[4] AS span_days FROM r WHERE q[1] - q[4] > 280
```

| 代號 | as_of | TTM EPS | 用到的 4 季 |
| --- | --- | ---: | --- |
| 1103 | 2001-01-01 | −0.40 | 2000Q3、2000Q2、**1999Q4、1999Q3**（缺 2000Q1） |
| 2330 | 2001-01-01 | 5.76 | 同上 |

## 根因

- **L1**：[`app/service.py`](../../app/service.py) `_ttm_value`（139–149 行）把 `as_of` 前所有有值的季排序後取前 4 個（`eligible[:4]`），只檢查個數不檢查連續；缺 2000Q1 時就往前多拿 1999Q3。
- **L0**：FinMind `TaiwanStockFinancialStatements` 沒有這兩家公司 2000Q1 的資料。

同一個寫法在另外兩處：

| 位置 | 用途 | 影響 |
| --- | --- | --- |
| `app/service.py` 441–442 行 `eligible_mr[:12]`、`[12:24]` | FinMind 月營收 TTM、TTM YoY | 上游缺月時跨 13 個月（目前 `raw_monthly_revenue` 只有 Q013 補的 18 列，沒有掃到） |
| `app/service.py` 654–655 行 `eligible[:12]`、`[12:24]` | TWSE／MOPS 月營收 TTM、TTM YoY | **就是 I023 的 TTM 錯值機制**：MOPS 9 月頁截斷 → 缺 9 月 → TTM 跨 13 個月 |

## 同類掃描

| 範圍 | 結果 |
| --- | --- |
| `raw_yearly_financials` 76 列有 4 季的 TTM | 2 列不連續（上表） |
| `monthly_revenue_twse` | I023 的 6 列（as_of 2026-11-10 的 TTM 缺 9 月）；其餘沒有缺月 |
| `grep -n "\[:4\]\|\[:12\]\|\[12:24\]" app/*.py` | 只有上表三處 |

## 修復方案

- **改哪些檔**：`app/service.py`：TTM 只在「最近 N 期連續」時計算（季：相鄰兩季差 3 個月；月：相鄰差 1 個月），不連續回 `None` 並在回應附 `ttm_gap`（缺哪一期）。三處抽成同一個 helper。
- **測試**：`tests/source/` 對三個函式各加缺一期的 fixture，斷言回 `None`。
- **_v2 SQL**：不需要（欄位不變，值變成 NULL）。
- **影響的既有列**：`raw_yearly_financials` 2 列（FinMind 2 次）；`monthly_revenue_twse` 6 列與 I023 一起重爬。
- **重打 API 次數**：FinMind 2 次＋I023 的約 30 次 MOPS。
- **復發報警**：Q035 C5（TTM 4 季連續，窗口內）；I023 的 Q013 C3（營收月唯一）間接抓得到月營收缺月。

## db/poc 規則檢查

不動 SQL，不適用。

## 修復紀錄

## 重爬與驗證
