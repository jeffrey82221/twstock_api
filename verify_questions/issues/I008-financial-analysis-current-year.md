---
id: I008
title: financial_analysis_year_list 產生尚未公布的當年度年報列
severity: S4
layer: L3
status: closed
pop_tables: [raw_financial_analysis, financial_analysis]
seeds: [financial_analysis_year_list]
found_by: [Q003]
---

## 症狀（附可重現的 SQL）

年報（MOPS t51sb02）隔年才公布，seed 卻包含當年度 2026：`raw_financial_analysis` 2026 年 19／19 列（指令 1 盤點）全部 NULL，
Q003 的三檔同樣全部 NULL。每列都是必定作廢的 API 呼叫，且 seed 列一旦 insert 就凍結，明年公布後也不會自動更新。

```sql
SELECT year, count(*) AS total, count(*) FILTER (WHERE financial_analysis IS NULL) AS null_rows
FROM pop.raw_financial_analysis GROUP BY 1 ORDER BY 1 DESC LIMIT 3
```

## 證據
- [`db/poc/financial_analysis_year_list.sql`](../../db/poc/financial_analysis_year_list.sql)：`generate_series(…, EXTRACT(YEAR FROM CURRENT_DATE))`，包含當年度。
- `raw_financial_analysis` 2026 年 21／21 列 NULL。

## 根因

seed 的時間邊界超過資料源實際覆蓋（rule 13）：年報要隔年第 2 季才可得。L3。

## 修復方案

1. seed 終點改為 `EXTRACT(YEAR FROM CURRENT_DATE - INTERVAL '4 months')::INT - 1`（每年 5 月起才納入前一年度）。
2. rule 18：欄位不變，只換 hidden view，pop 資料保留（`pop_update_mat_views` 對 seed 只做 replace_hidden_view）。
3. 已凍結的 2026 年 21 列以 crawl_delete_rows 刪除（pin 後 0 次 API 呼叫）；明年 5 月 seed 重新產生 2026 年時再由 cron 爬取。

## 修復紀錄（2026-11-25，指令 5）

- `db/poc/financial_analysis_year_list.sql`：終點改為 `EXTRACT(YEAR FROM CURRENT_DATE - INTERVAL '4 months')::INT - 1`；`db/poc/README.md` 同步。
- 上線：`poc_rebuild_views` → `pop_update_mat_views(financial_analysis_year_list)`：`replace_hidden_view`，pop 資料保留。

## 重爬與驗證

- `hidden.financial_analysis_year_list` 2026 年列數：0。
- crawl_delete_rows `{year: 2026}`：刪 21 列、pin 21、0 次 API。
- 斷言：Q003 C2 PASS。
