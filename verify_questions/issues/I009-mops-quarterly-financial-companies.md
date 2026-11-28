---
id: I009
title: 金融股（如 2882）沒有季財務資料：adapter 要求營益分析表有該股
severity: S2
layer: L1
status: closed
pop_tables: [raw_mops_quarterly_financials, mops_quarterly_financials]
seeds: [mops_quarterly_financials_quarter_list]
found_by: [Q003]
---

## 症狀（附可重現的 SQL）

指令 4 診斷 [I004](I004-normalized-as-of-from-response.md) 時發現：[Q003](../questions/Q003-quality-roe-debt.md) 補爬後 `raw_mops_quarterly_financials` 新增 12 列 NULL，全部是 2882（國泰金），每一季都是 HTTP 404。
[Q003](../questions/Q003-quality-roe-debt.md) C4 的可比對列數因此不含 2882，斷言沒有直接抓到。

```sql
SELECT r.stk_code, count(*) AS total, count(*) FILTER (WHERE r.quarterly_financials IS NULL) AS null_rows
FROM pop.raw_mops_quarterly_financials r GROUP BY 1 HAVING count(*) FILTER (WHERE r.quarterly_financials IS NULL) > 0
```

## 證據

- `curl localhost:5002/api/company/2882/quarterly-financials?as_of=2025-09-30` → `no quarterly financial data … (reason=not_found)`。
- [`app/mops_quarterly_financials.py`](../../app/mops_quarterly_financials.py) `_scan_quarters`：`if stock not in operating: continue`——只要 t163sb06（營益分析彙總）沒有該股就跳過，即使 t163sb04（綜合損益表）有 EPS。營益分析表不含金融業（毛利率等對金融業不適用）。

## 根因

adapter 以營益分析表為必要條件，金融業只出現在綜合損益表，因此永遠 not_found。L1。

## 修復方案

1. `_scan_quarters`：營益分析表沒有該股、但綜合損益表有時，回 `found=True`，比率與營收為 null、`eps` 有值。
2. 測試：`tests/source/test_mops_quarterly_financials_source.py` 新增「只在綜合損益表出現」的案例。
3. 重爬：2882 的 12 列（與 [I001](I001-mops-quarterly-financials-ytd.md) 一起重爬）。

## 修復紀錄（2026-11-25，指令 5）

- `app/mops_quarterly_financials.py`：`_ytd_row()` 在營益分析表沒有該股、但綜合損益表有時仍回傳（營收／比率 null、只有 EPS）；`_scan_quarters` 改用它判斷是否找到。
- 測試：`test_financial_company_found_with_eps_only`。

## 重爬與驗證

- 直接呼叫：2882 2025-09-30 → found、Q3 單季 EPS 1.95。
- 與 [I001](I001-mops-quarterly-financials-ytd.md) 一起重爬：2882 的 12 列 NULL → 0 列。[Q003](../questions/Q003-quality-roe-debt.md) C4 可比對組數 15 → 17（含 2882）並 PASS。
