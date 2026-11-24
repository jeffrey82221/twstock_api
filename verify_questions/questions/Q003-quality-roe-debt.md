---
id: Q003
title: 2330、2882、1101 是否連續 3 年 ROE > 15% 且負債比 < 50%？
strategy: quality
status: failing
stocks: ["2330", "2882", "1101"]
window: {since: 2021, until: 2025}
pop_tables: [financial_analysis, mops_quarterly_financials]
crawl:
  - seed: company_list                       # 1101 尚未進母體（§8.1 依賴鏈）
    filters: {stk_code: "1101"}
    budget: 1
  - seed: company_basic_info_list
    filters: {stk_code: "1101"}
    budget: 1
  - seed: financial_analysis_year_list
    filters: {stk_code: ["2330", "2882", "1101"]}
    order_by: ["year"]
    budget: 18               # MOPS；6 年 × 3 檔
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["2330", "2882", "1101"]}
    order_by: ["quarter_end"]
    budget: 24               # MOPS；8 季 × 3 檔（2024–2025）
issues: [I001, I008]
last_run: 2026-11-25
---

## 決策情境
2023–2025 每年 ROE > 15% 且負債比 < 50% → 高品質體質，可長期持有；金融股（2882）負債比天生高，
只看 ROE，這是邊界案例。1101 尚未進母體，驗證依賴鏈補爬。

## 決策 SQL
```sql
SELECT stk_code,
       count(*) FILTER (WHERE year BETWEEN 2023 AND 2025 AND roe IS NOT NULL) AS n_years,
       min(roe) FILTER (WHERE year BETWEEN 2023 AND 2025) AS min_roe_3y,
       max(debt_ratio) FILTER (WHERE year BETWEEN 2023 AND 2025) AS max_debt_ratio_3y,
       bool_and(roe > 15) FILTER (WHERE year BETWEEN 2023 AND 2025) AS roe_gt15_3y,
       bool_and(debt_ratio < 50) FILTER (WHERE year BETWEEN 2023 AND 2025) AS debt_lt50_3y
FROM pop.financial_analysis
WHERE stk_code IN ('2330','2882','1101')
GROUP BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 2021–2025 每年都有 ROE 與負債比
```sql
WITH e AS (
  SELECT s.stk_code, y AS year
  FROM unnest(ARRAY['2330','2882','1101']) AS s(stk_code) CROSS JOIN generate_series(2021, 2025) AS y
)
SELECT e.stk_code, e.year, f.roe, f.debt_ratio
FROM e LEFT JOIN pop.financial_analysis f ON f.stk_code = e.stk_code AND f.year = e.year
WHERE f.roe IS NULL OR f.debt_ratio IS NULL
```

### C2 availability — 這幾檔所有已爬的年度財務比率 raw 列都可用
```sql
SELECT stk_code, year, financial_analysis->>'reason' AS reason
FROM pop.raw_financial_analysis
WHERE stk_code IN ('2330','2882','1101')
  AND (financial_analysis IS NULL OR financial_analysis->>'found' = 'false')
```

### C3 validity — 比率在合理範圍（%）
```sql
SELECT stk_code, year, roe, roa, debt_ratio, net_profit_margin, current_ratio
FROM pop.financial_analysis
WHERE stk_code IN ('2330','2882','1101')
  AND (roe < -100 OR roe > 100 OR roa < -100 OR roa > 100 OR debt_ratio < 0 OR debt_ratio > 100
       OR net_profit_margin < -1000 OR net_profit_margin > 100 OR current_ratio < 0)
```

### C4 cross-source — 年度 EPS（t51sb02）= 同年 MOPS 四季 EPS 加總（誤差 2%）
```sql
WITH q AS (
  SELECT stk_code, fiscal_year::int AS y, sum(eps) AS eps_4q, count(*) AS n_q
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2882','1101') GROUP BY 1, 2
)
SELECT f.stk_code, f.year, f.eps AS annual_eps, q.eps_4q
FROM pop.financial_analysis f JOIN q ON q.stk_code = f.stk_code AND q.y = f.year
WHERE q.n_q = 4 AND abs(f.eps - q.eps_4q) > 0.02 * abs(f.eps)
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.financial_analysis f
JOIN (SELECT stk_code, fiscal_year::int AS y FROM pop.mops_quarterly_financials
      WHERE stk_code IN ('2330','2882','1101') GROUP BY 1, 2 HAVING count(*) = 4) q
  ON q.stk_code = f.stk_code AND q.y = f.year
```

## 已知限制
- 金融股的負債比通常 > 85%，決策時對 2882 只看 ROE；C3 的範圍檢查仍適用。
- 年報（t51sb02）在隔年才公布，當年度（2026）不應出現在資料中。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-25 | FAIL（C2、C4） | [run](../runs/2026-11-25_Q003.md) | financial_analysis 可信；當年度空列、季 EPS 累計 |
