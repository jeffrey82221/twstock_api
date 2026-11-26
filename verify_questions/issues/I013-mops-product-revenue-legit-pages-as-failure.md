---
id: I013
title: MOPS 產品營收把「營收淨額 0」與「外國發行人免申報」誤判成上游失敗
severity: S3
layer: L1（adapter：_parse_mops_table、get_product_revenue）
status: closed
pop_tables: [raw_product_revenue, product_revenue]
seeds: [product_revenue_filer_list]
found_by: [2026-11-26 retry_failed 後殘留的 2 列]
---

## 症狀（附可重現的 SQL）

```sql
SELECT stk_code, ym, product_revenue->>'found', product_revenue->>'error', product_revenue->>'notes'
FROM pop.raw_product_revenue
WHERE product_revenue IS NULL OR product_revenue ? 'source_errors' OR product_revenue->>'found' = 'false'
```

修復前 2 列，重打仍然失敗（不是暫時性問題）：

- 5206 民國 111/03：`source_errors`「t05st08 detail unparsed」，被標成限流。
- 2637 民國 115/10：error「MOPS step 2 未含 autoForm，也未含資料表。」

## 證據

- 5206（坤悅，建商）的明細頁是合法申報：只有「(1) 營建」、金額空白，營收淨額 `0`。`_parse_mops_table` 用 `if items or total_revenue` 判斷，`0` 是 falsy，所以 `found=False`。
- 2637（慧洋-KY）的單一公司頁與明細頁都只有一句「外國發行人免申報本項資訊」。MOPS 全市場申報名單（`ajax_t05st08_all`）照樣列出 KY 公司，所以它會進 seed。adapter 只認「該公司無申報」「無此資料」，其他一律當錯誤。
- 同一路徑的 I010 類問題：`get_product_revenue` 把 redirectToOld 失敗、step 2 解析失敗都寫進 `_product_revenue_cache`，而 step 3 解析失敗時被當成「未申報」快取，`_get_last_filed_ym` 會因此誤以為這家公司從未申報。

## 根因

L1：adapter 沒有窮舉 MOPS 的明確無資料頁，數值判斷又用了 truthiness。

## 修復紀錄

- `app/sources.py`：
  - `_MOPS_T05ST08_NO_DATA` 列出明確無資料頁（外國發行人免申報、該公司無申報、無此資料），由 `_mops_t05st08_no_data()` 判斷，`_parse_mops_table` 回 `no_data=True`。
  - `found` 改為 `items or total_revenue is not None`。
  - `get_product_revenue_at`：明細頁明確無資料時快取並回 notes，不記 source error。
  - `get_product_revenue`：redirectToOld 失敗、step 2 或 step 3 解析失敗時記 `record_bad_payload`、不快取；只快取成功或明確無資料的結果。
  - 「免申報」的 notes 經 `_last_filed_notes` 傳到 `get_product_revenue_at`。
- `tests/source/test_product_revenue_at_source.py`：新增免申報頁、營收 0、錯誤頁不算無資料、免申報不記 source error、notes 傳遞，共 5 個測試。

## 重爬與驗證

`retry_failed.py raw_product_revenue --apply --include-not-found`（2026-11-26）：

- 5206 111/03 → `found=true`、`total_revenue=0`（`product_revenue` 只展開 items，所以 0 列是正確的）。
- 2637 115/10 → `found=false`、`error=null`、notes「外國發行人免申報各項產品業務營收。」。這是合法無資料，`pop_raw_quality` 仍會算成不可用（1 列）。
- `raw_product_revenue`：上游失敗 2 → 0。
