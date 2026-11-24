---
id: I005
title: 上櫃股沒有估值資料（company_valuation 只涵蓋上市）
severity: S2
layer: L1（需新上游）
status: diagnosed
pop_tables: [raw_company_valuation, company_valuation]
seeds: [company_valuation_monthly_list]
found_by: [Q001]
---

## 症狀（附可重現的 SQL）

`raw_company_valuation` 的 NULL 全部是上櫃股，上櫃股的可用列數為 0（211／211 列 NULL）。`/api/company-valuation` 的上游 TWSE BWIBBU_d 只涵蓋上市。
上櫃股的估值判斷（PER 百分位、殖利率）完全無法進行；seed 仍對每檔上櫃股每月打一次 API（S2 + S4）。

```sql
SELECT b.market, count(*) AS raw_rows, count(*) FILTER (WHERE r.company_valuation IS NULL) AS null_rows
FROM pop.raw_company_valuation r JOIN pop.company_basic_info_list b USING (stk_code) GROUP BY 1
```

## 證據
- [`app/valuation_source.py`](../../app/valuation_source.py) 只有 TWSE BWIBBU_d（`_fetch_twse_bwibbu_day`），沒有任何 TPEx 路徑；上櫃股一律回 NULL（211／211）。

## 根因

adapter 只實作上市來源。TPEx 有對應的「上櫃股票個股本益比、殖利率及股價淨值比」日資料，尚未串接。L1。

## 修復方案

需要先做上游研究（status 維持 diagnosed，不進本輪指令 5）：
1. 確認 TPEx 個股本益比日資料的 URL、格式與最早可得日，先補 `tests/upstream/` contract 測試。
2. `valuation_source.py` 依市場分流（同 `institutional_source._fetch_per_day_market` 的作法），回應格式不變。
3. 過渡期（S4 止血）：可考慮讓 `company_valuation_monthly_list` 只產生上市股，但這是 rule 13「WHERE 篩選」的例外，需使用者同意。
4. 修好後：上櫃股 211 列 NULL 重爬（crawl_delete_rows 會 pin，之後重爬拿新回應）。

## 修復紀錄

## 重爬與驗證
