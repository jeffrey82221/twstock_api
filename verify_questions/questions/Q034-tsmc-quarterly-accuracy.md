---
id: Q034
title: 2330 近 4 季營收是否每季年增 > 30%？DB 的季營收與季 EPS 和台積電財報新聞稿一樣嗎？
strategy: growth             # §5.1 成長
status: passing
stocks: ["2330"]
window: {since: 2024-01-01, until: 2026-09-30}
pop_tables: [mops_quarterly_financials]
crawl: []                    # 出題時（2026-11-27）2330 2022Q1–2026Q3 都已在 pop
issues: []
last_run: 2026-11-28
---

## 決策情境
最近 4 季（2025Q4–2026Q3）每季營收年增 > 30% → 「成長加速」，續抱；任一季 < 10% → 重新檢視。C1、C2 FAIL 時結論不可信。

## 決策 SQL
```sql
WITH q AS (
  SELECT DISTINCT fiscal_year, quarter, revenue_millions FROM pop.mops_quarterly_financials
  WHERE stk_code = '2330' AND revenue_millions IS NOT NULL
)
SELECT c.fiscal_year, c.quarter, c.revenue_millions, p.revenue_millions AS prev_year,
       round(100.0 * (c.revenue_millions / p.revenue_millions - 1), 1) AS yoy_pct
FROM q c JOIN q p ON p.fiscal_year = c.fiscal_year - 1 AND p.quarter = c.quarter
WHERE (c.fiscal_year, c.quarter) >= (2025, 4)
ORDER BY c.fiscal_year, c.quarter
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — 季營收（MOPS，百萬元）= 台積電財報新聞稿（誤差 = 新聞稿精度的一半）
```sql
WITH golden(y, q, revenue_millions, tol) AS (VALUES
  (2025, 4, 1046090, 5),     -- 出處：台積電 2025Q4 財報（ETtoday finance.ettoday.net/news/3101599：合併營收約 1 兆 460 億 9 千萬元，精度千萬元），取得於 2026-11-27
  (2026, 2, 1270400, 50),    -- 出處：台積電 2026Q2 財報（旺得富 wantrich.chinatimes.com/news/20260716900372-420101：約 1 兆 2,704 億元，精度億元），取得於 2026-11-27
  (2026, 3, 1494200, 50)     -- 出處：台積電 2026Q3 財報（科技新報 finance.technews.tw/2026/10/15/tsmcs-third-quarter-eps-hit-a-new-high-of-nt29-43/：約 1 兆 4,942 億元），取得於 2026-11-27
)
SELECT g.y, g.q, g.revenue_millions AS golden, m.revenue_millions AS db
FROM golden g
LEFT JOIN (SELECT DISTINCT fiscal_year, quarter, revenue_millions FROM pop.mops_quarterly_financials WHERE stk_code = '2330') m
  ON m.fiscal_year = g.y AND m.quarter = g.q
WHERE m.revenue_millions IS NULL OR abs(m.revenue_millions - g.revenue_millions) > g.tol
```
可比對列數（預期 3）：
```sql
SELECT count(*) FROM (VALUES (2025, 4), (2026, 2), (2026, 3)) g(y, q)
JOIN (SELECT DISTINCT fiscal_year, quarter FROM pop.mops_quarterly_financials WHERE stk_code = '2330' AND revenue_millions IS NOT NULL) m
  ON m.fiscal_year = g.y AND m.quarter = g.q
```

### C2 accuracy — 2026 上半年營收（Q1＋Q2 加總）= 公告的上半年累計值（誤差 1 百萬元）；比對範圍與 C1 不同（缺季或重複季會讓加總對不上）
```sql
WITH m AS (SELECT DISTINCT fiscal_year, quarter, revenue_millions FROM pop.mops_quarterly_financials
           WHERE stk_code = '2330' AND fiscal_year = 2026 AND quarter IN (1, 2))
SELECT 2404484 AS golden_h1_millions,   -- 出處：旺得富 wantrich.chinatimes.com/news/20260721900276-420101（上半年合併營收 2 兆 4,044.84 億元），取得於 2026-11-27
       (SELECT sum(revenue_millions) FROM m) AS db_h1, (SELECT count(*) FROM m) AS n_quarters
WHERE (SELECT count(*) FROM m) <> 2 OR abs((SELECT sum(revenue_millions) FROM m) - 2404484) > 1
```
可比對列數（預期 2 季）：
```sql
SELECT count(DISTINCT quarter) FROM pop.mops_quarterly_financials WHERE stk_code = '2330' AND fiscal_year = 2026 AND quarter IN (1, 2)
```

### C3 accuracy — 季 EPS = 新聞稿 EPS（誤差 0.015 元：新聞稿是稀釋 EPS、MOPS 是基本 EPS，台積電兩者最多差 0.01，再留捨入）；比對範圍與 C1 不同
2025Q4（19.50 vs 19.51）、2026Q3（29.43 vs 29.42）差 0.01，與年度 2025（66.25 vs 66.26，Q012 已知限制）同一個定義差。
```sql
WITH golden(y, q, eps) AS (VALUES
  (2025, 3, 17.44),   -- 出處：經濟日報 money.udn.com/money/amp/story/5607/9074245（第 3 季 EPS 17.44 元），取得於 2026-11-27
  (2025, 4, 19.50),   -- 出處：ETtoday 同 C1（每股盈餘 19.5 元，稀釋）
  (2026, 2, 27.25),   -- 出處：旺得富同 C1（每股盈餘 27.25 元）
  (2026, 3, 29.43)    -- 出處：科技新報同 C1（每股盈餘 29.43 元，稀釋）
)
SELECT g.y, g.q, g.eps AS golden, m.eps AS db
FROM golden g
LEFT JOIN (SELECT DISTINCT fiscal_year, quarter, eps FROM pop.mops_quarterly_financials WHERE stk_code = '2330') m
  ON m.fiscal_year = g.y AND m.quarter = g.q
WHERE m.eps IS NULL OR abs(m.eps - g.eps) > 0.015
```
可比對列數（預期 4）：
```sql
SELECT count(*) FROM (VALUES (2025, 3), (2025, 4), (2026, 2), (2026, 3)) g(y, q)
JOIN (SELECT DISTINCT fiscal_year, quarter FROM pop.mops_quarterly_financials WHERE stk_code = '2330' AND eps IS NOT NULL) m
  ON m.fiscal_year = g.y AND m.quarter = g.q
```

### C4 completeness — 2024Q1–2026Q3 每季都有營收與 EPS
```sql
SELECT y, q FROM generate_series(2024, 2026) AS y CROSS JOIN generate_series(1, 4) AS q
WHERE (y, q) <= (2026, 3)
  AND NOT EXISTS (SELECT 1 FROM pop.mops_quarterly_financials m WHERE m.stk_code = '2330' AND m.fiscal_year = y AND m.quarter = q
                  AND m.revenue_millions IS NOT NULL AND m.eps IS NOT NULL)
```

### C5 uniqueness — 同一季只出現在一個 as_of（I019 的失效模式）
```sql
SELECT fiscal_year, quarter, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.mops_quarterly_financials WHERE stk_code = '2330' AND fiscal_year IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

## 已知限制
- golden 是新聞轉載的台積電財報新聞稿，營收只到千萬元或億元精度，C1 抓不到 5–50 百萬元以內的錯；C2 的上半年累計精度是百萬元，比較嚴。
- **EPS 定義**：台積電新聞稿報稀釋 EPS，MOPS 季報是基本 EPS，2025Q4、2026Q3 差 0.01 元。C3 的 0.015 容許只針對這個定義差，其他公司稀釋與基本差距可能更大，不能直接沿用。
- 2026Q1 的 EPS 只有推算值（上半年 49.32 − Q2 27.25 = 22.07，DB 22.08），沒有放進 golden。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-28 | passing（5／5） | [run](../runs/2026-11-28_Q034.md) | 不需補爬；季營收／EPS 對照新聞稿一致；2025Q4 年增 20.5% 未達加速 |
