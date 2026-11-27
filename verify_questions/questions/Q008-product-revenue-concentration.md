---
id: Q008
title: 1402、2308、3045、2882、1519 的營收是否過度集中在單一產品？產品營收和月營收對得起來嗎？
strategy: industry
status: failing
stocks: ["1402", "2308", "3045", "2882", "1519"]
window: {since: 2026-08-01, until: 2026-11-26}
pop_tables: [product_revenue, monthly_revenue_twse, company_business_items, chain_info_list, company_basic_info_list]
crawl:
  - seed: company_list                     # 1402、1519 還沒進母體（§8.1 依賴鏈）
    filters: {stk_code: ["1402", "1519"]}
    budget: 2
  - seed: company_basic_info_list
    filters: {stk_code: ["1402", "1519"]}
    budget: 2
  - seed: product_revenue_filer_list
    filters: {stk_code: ["1402", "2308", "3045", "2882", "1519"], ym: ["11508", "11509", "11510"]}
    order_by: ["report_month"]
    budget: 15               # MOPS t05st08；每檔最近 3 個月
  - seed: financial_month_twse_list
    filters: {stk_code: ["1402", "2308", "3045", "2882", "1519"]}
    order_by: ["as_of_date"]
    budget: 15               # TWSE／MOPS t21sc03；2026-09-10、10-10、11-10 → 8、9、10 月營收
issues: [I022]
last_run: 2026-11-27
---

## 決策情境
最近一個月第一大產品占營收 > 70% → 標記「單一產品集中」，產業鏈景氣反轉時優先檢視；
同時看營業項目（`company_business_items`）與所屬產業鏈數量，判斷是否有其他業務可分散。
產品營收不能和月營收對上（C3 FAIL）時，結論不可信。

## 決策 SQL
```sql
WITH latest AS (
  SELECT DISTINCT ON (stk_code) stk_code, ym, report_month
  FROM pop.product_revenue
  WHERE stk_code IN ('1402','2308','3045','2882','1519')
  ORDER BY stk_code, report_month DESC
)
SELECT l.stk_code, l.ym,
       (SELECT p.name FROM pop.product_revenue p WHERE p.stk_code = l.stk_code AND p.ym = l.ym ORDER BY p.amount DESC LIMIT 1) AS top_product,
       (SELECT max(p.percentage) FROM pop.product_revenue p WHERE p.stk_code = l.stk_code AND p.ym = l.ym) AS top_share_pct,
       (SELECT count(*) FROM pop.product_revenue p WHERE p.stk_code = l.stk_code AND p.ym = l.ym) AS n_products,
       (SELECT count(*) FROM pop.company_business_items c WHERE c.stock_id = l.stk_code) AS n_business_items,
       (SELECT count(DISTINCT ic_code) FROM pop.chain_info_list ci WHERE ci.stk_code = l.stk_code) AS n_chains
FROM latest l
ORDER BY l.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 internal — 各產品金額合計 − 銷貨退回 = 營收合計（誤差 1%）
```sql
SELECT stk_code, ym, max(total_revenue) AS total_revenue, max(sales_return) AS sales_return,
       sum(amount) AS sum_amount, count(*) AS n_products
FROM pop.product_revenue
WHERE stk_code IN ('1402','2308','3045','2882','1519')
GROUP BY stk_code, ym
HAVING abs(sum(amount) - COALESCE(max(sales_return), 0) - max(total_revenue)) > 0.01 * max(total_revenue)
```

### C2 validity — 名次是 (1)–(10) 或「其他」、名稱非空、金額非 0、營收淨額非負（單項占比不設上下限：MOPS 允許負數項目，金融業單項可超過營收淨額）
```sql
SELECT stk_code, ym, rank, name, amount, total_revenue
FROM pop.product_revenue
WHERE stk_code IN ('1402','2308','3045','2882','1519')
  AND (rank !~ '^(\(([1-9]|10)\)|其他)$' OR coalesce(btrim(name), '') = '' OR amount IS NULL OR amount = 0
       OR total_revenue IS NULL OR total_revenue < 0 OR report_month IS NULL)
```

### C3 cross-source — 產品營收合計（MOPS t05st08）= 同月月營收（TWSE／MOPS t21sc03）（誤差 2%，兩者單位都是元）
```sql
SELECT p.stk_code, p.ym, p.report_month, max(p.total_revenue) AS product_total, m.latest_month_value AS monthly_revenue,
       round(max(p.total_revenue) / NULLIF(m.latest_month_value, 0), 4) AS ratio
FROM pop.product_revenue p
JOIN pop.monthly_revenue_twse m ON m.stk_code = p.stk_code AND date_trunc('month', m.data_date) = p.report_month
WHERE p.stk_code IN ('1402','2308','3045','2882','1519')
GROUP BY p.stk_code, p.ym, p.report_month, m.latest_month_value
HAVING abs(max(p.total_revenue) - m.latest_month_value) > 0.02 * m.latest_month_value
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(DISTINCT (p.stk_code, p.ym)) FROM pop.product_revenue p
JOIN pop.monthly_revenue_twse m ON m.stk_code = p.stk_code AND date_trunc('month', m.data_date) = p.report_month
WHERE p.stk_code IN ('1402','2308','3045','2882','1519')
```

### C4 availability — 每檔都有營業項目，且屬於至少一條產業鏈
```sql
SELECT s.stk_code,
       (SELECT count(*) FROM pop.company_business_items c WHERE c.stock_id = s.stk_code) AS n_business_items,
       (SELECT count(*) FROM pop.chain_info_list ci WHERE ci.stk_code = s.stk_code) AS n_chain_rows
FROM unnest(ARRAY['1402','2308','3045','2882','1519']) AS s(stk_code)
WHERE NOT EXISTS (SELECT 1 FROM pop.company_business_items c WHERE c.stock_id = s.stk_code)
   OR NOT EXISTS (SELECT 1 FROM pop.chain_info_list ci WHERE ci.stk_code = s.stk_code)
```

### C5 uniqueness — 同一檔、同一月、同一名次只有一列
```sql
SELECT stk_code, ym, rank, count(*) AS n
FROM pop.product_revenue
WHERE stk_code IN ('1402','2308','3045','2882','1519')
GROUP BY stk_code, ym, rank
HAVING count(*) > 1
```

## 已知限制
- C1 FAIL 的根因已確認是 adapter 丟掉負數項目（I022，L1），不是 MOPS 只列前幾名。
- MOPS 的產品列可以是負數：一般公司是「產品別調整」（1519 112/06 的備註原文），金融業是損失科目；金融業的單項也會超過營收淨額（2882 115/10 利息淨收益 > 營收淨額）。所以 C2 不限制單項占比範圍（2026-11-27 改寫，原寫法「每項介於 0 與 100」不符合現實），占比加總交給 C1。
- 決策門檻「第一大產品 > 70%」對金融業沒有意義（收益科目有正有負），金融業的集中度結論一律不採用。
- C3 的可比對列數預期 = 檔數 × 月數（本題 15）。少於預期時要追：2026-11-27 只有 12，追到 I023（月營收缺 9 月）。
- 2882 為金融業，「產品」是收益科目；C1／C2 若只在金融業 FAIL，先查 MOPS 原始頁面。
- `rank` 欄的「其他」列不在 1..N 名次內，C5 仍要求唯一。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | failing（C1 FAIL，其餘 PASS） | [run](../runs/2026-11-27_Q008.md) | 開 I022、I023；C2 依現實改寫；1519 變壓器 78.27% → 單一產品集中 |
