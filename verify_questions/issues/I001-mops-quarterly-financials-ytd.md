---
id: I001
title: mops_quarterly_financials 的營收與 EPS 是年初至今累計值，不是單季
severity: S1
layer: L1
status: closed
pop_tables: [mops_quarterly_financials, raw_mops_quarterly_financials]
seeds: [mops_quarterly_financials_quarter_list]
found_by: [Q002, Q003]
---

## 症狀（附可重現的 SQL）

`mops_quarterly_financials` 一列代表一季（`fiscal_year`、`quarter`），`revenue_millions`、`eps` 卻是年初至今累計：
Q2–Q4 的值約等於前幾季加總。拿來做「季毛利率／季 EPS 連升」或季營收比較會得到錯誤結論，且數字看起來完全正常（S1）。

```sql
SELECT stk_code, fiscal_year, quarter, revenue_millions, eps
FROM pop.mops_quarterly_financials
WHERE stk_code = '2330' AND fiscal_year IN (2025, 2026) ORDER BY fiscal_year, quarter
```
2330：2026 Q1 1,134,103／22.08 → Q2 2,404,483／49.33 → Q3 3,898,727／78.75；2025 Q4 EPS 66.26 = 全年 EPS（`financial_analysis` 2025）。

- Q002 C5：季營收 vs 該季三個月月營收合計，12 組可比對、9 組不符（通過的都是 Q1）。
- Q003 C4：年 EPS vs 四季 EPS 加總，15 組全部不符（加總約為年 EPS 的 2.3 倍）。

## 證據
- 直接相減驗證：2330 2026「Q2」`revenue_millions` 2,404,483.69 = Q1 1,134,103.44 + 4–6 月月營收合計 1,270,380.25（完全吻合）；2025「Q4」EPS 66.26 = `financial_analysis` 2025 全年 EPS。
- raw JSON（`raw_mops_quarterly_financials` 2330 2026-06-30）：`quarter: 2`、`revenue_millions: 2404483.69`、`eps: 49.33`，沒有任何「累計」標示。
- [`app/mops_quarterly_financials.py`](../../app/mops_quarterly_financials.py) `_scan_quarters`：把 t163sb06（營益分析）與 t163sb04（綜合損益表）的列原樣放進 `data`，沒有換算。

## 根因

MOPS t163sb06／t163sb04 的彙總表是**年初至今累計**（Q2 = 1–6 月、Q4 = 全年），adapter 原樣輸出並標成單季（`quarter`），endpoint 說明也寫「季度財務資料」。層級 L1（source adapter）。

## 修復方案

1. `app/mops_quarterly_financials.py`：Q2–Q4 另取同市場、同年的前一季（已有 `_previous_period`，HTML 依 (市場, 年, 季) 磁碟快取，幾乎不增加上游請求），換算單季值：
   - `revenue_millions_q = rev_ytd(q) − rev_ytd(q−1)`；`eps_q = eps_ytd(q) − eps_ytd(q−1)`
   - 比率先還原成金額再相減：`margin_q = (margin_ytd(q)·rev_ytd(q) − margin_ytd(q−1)·rev_ytd(q−1)) / rev_q`
   - `data` 改為單季值，原累計值放 `data_ytd`；Q1 不變。前一季缺資料時單季欄位為 null（不得以累計值冒充）。
2. rule 18：正規化 view `mops_quarterly_financials` 欄位與主鍵不變，只修正值的語意 → 就地修正、不需 `_v2`。
3. 測試：`tests/source/test_mops_quarterly_financials_source.py` 新增 Q2／Q4 換算與前一季缺資料的案例。
4. 重爬：`mops_quarterly_financials_quarter_list` 全部 355 列（crawl_delete_rows 依 stk_code 分批、pin 回應）→ crawl_insert_filtered。MOPS HTML 走磁碟快取，預估真實上游請求 < 50。

## 修復紀錄（2026-11-25，指令 5）

- `app/mops_quarterly_financials.py`：新增 `single_quarter()`；Q2–Q4 另取同年前一季（磁碟快取）換算單季，`data` = 單季、`data_ytd` = 累計；前一季下載失敗回 upstream_error，前一季缺資料時單季欄位為 null。
- `app/schemas.py`、`app/main.py`：回應新增 `data_ytd` 與說明。`db/poc/mops_quarterly_financials.sql` 欄位不變（rule 18 就地修正），註解說明單季語意；`db/poc/README.md` 同步。
- 測試：`tests/source/test_mops_quarterly_financials_source.py` +6（單季換算、Q4 = 全年 − 9M、Q1 不多抓、前一季缺／失敗）。全套離線測試 420 passed。

## 重爬與驗證

- 直接呼叫：2330 2026 Q2 → `revenue_millions` 1,270,380.25（= 4–6 月月營收合計）、EPS 27.25；`data_ytd` 2,404,483.69／49.33。
- crawl_delete_rows：`mops_quarterly_financials_quarter_list` 355 列（16 檔）、pin 355、0 次 API；接著 `pop_update_mat_views` 在 seed 為空時重建 `mops_quarterly_financials`（I004 一起），0 次重打。
- 重爬：依 http_log 還原原本的 356 組 (stk_code, 季末日)（比刪除多 1 組，為先前 rollback 過的請求），全部 insert；1101 一批遇 15 秒傳輸逾時、整批 rollback 後以 row_cnt=2 重試成功。結果 356 列、1 列 NULL（8463 2013-Q1，上市前，404 正常）。
- 斷言：Q002 C5 PASS（12 組可比對）、Q003 C4 PASS（17 組可比對）。
