---
id: Q039
title: 2454 聯發科 2026Q3 營收與 EPS 是否比去年同季成長？DB 的季營收、季 EPS 和法說會公布的一樣嗎？
strategy: growth             # §5.1 成長
status: draft
stocks: ["2454"]
window: {since: 2025-01-01, until: 2026-09-30}
pop_tables: [mops_quarterly_financials]
crawl: []                    # 出題時（2026-11-28）2454 2023Q1–2026Q3 由 Q012 補過
issues: []
last_run: null
---

## 決策情境
2026Q3 營收年增 > 15% 且 EPS 年增 > 5% → 「恢復成長」（2454 2025 年 EPS 衰退，Q012）；任一項衰退 → 不列入。C1、C2 FAIL 時結論不可信。

## 決策 SQL
```sql
WITH q AS (SELECT DISTINCT fiscal_year, quarter, revenue_millions, eps FROM pop.mops_quarterly_financials WHERE stk_code = '2454')
SELECT c.revenue_millions, p.revenue_millions AS prev_rev, round(100.0 * (c.revenue_millions / p.revenue_millions - 1), 1) AS rev_yoy_pct,
       c.eps, p.eps AS prev_eps, round(100.0 * (c.eps / p.eps - 1), 1) AS eps_yoy_pct
FROM q c JOIN q p ON p.fiscal_year = 2025 AND p.quarter = 3
WHERE c.fiscal_year = 2026 AND c.quarter = 3
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — 2026Q3 季營收（百萬元）= 法說會公布值（1,680.58 億元，精度百萬元，±1 百萬）
```sql
WITH m AS (SELECT DISTINCT revenue_millions FROM pop.mops_quarterly_financials WHERE stk_code = '2454' AND fiscal_year = 2026 AND quarter = 3)
SELECT 168058 AS golden_millions,   -- 出處：聯發科 2026Q3 法說會（科技新報 finance.technews.tw/2026/10/30/mediateks-third-quarter-eps-reached-nt17-5-with-a-gross-margin-of-46-3/：合併營收 1,680.58 億元），取得於 2026-11-28
       (SELECT revenue_millions FROM m) AS db
WHERE (SELECT revenue_millions FROM m) IS NULL OR abs((SELECT revenue_millions FROM m) - 168058) > 1
```
可比對列數（預期 1）：
```sql
SELECT count(*) FROM (SELECT DISTINCT revenue_millions FROM pop.mops_quarterly_financials
                      WHERE stk_code = '2454' AND fiscal_year = 2026 AND quarter = 3 AND revenue_millions IS NOT NULL) m
```

### C2 accuracy — 季 EPS = 法說會公布值（誤差 0.015：Q2–Q4 的單季 EPS 是 MOPS 累計值相減得來，I001，相減會有 ±0.01 的捨入）；比對範圍與 C1 不同
2026Q2：法說會 15.28、DB 15.27（= 上半年累計 − Q1）。稀釋 EPS 不會大於基本 EPS，所以這 0.01 不是稀釋／基本的差，是相減捨入。
```sql
WITH golden(y, q, eps) AS (VALUES
  (2025, 3, 15.84),   -- 出處：科技新報同 C1（去年同期 15.84 元）
  (2026, 2, 15.28),   -- 出處：科技新報同 C1（上季 15.28 元）
  (2026, 3, 17.50)    -- 出處：科技新報同 C1（每股盈餘 17.5 元）
)
SELECT g.y, g.q, g.eps AS golden, m.eps AS db
FROM golden g
LEFT JOIN (SELECT DISTINCT fiscal_year, quarter, eps FROM pop.mops_quarterly_financials WHERE stk_code = '2454') m
  ON m.fiscal_year = g.y AND m.quarter = g.q
WHERE m.eps IS NULL OR abs(m.eps - g.eps) > 0.015
```
可比對列數（預期 3）：
```sql
SELECT count(*) FROM (VALUES (2025, 3), (2026, 2), (2026, 3)) g(y, q)
JOIN (SELECT DISTINCT fiscal_year, quarter FROM pop.mops_quarterly_financials WHERE stk_code = '2454' AND eps IS NOT NULL) m
  ON m.fiscal_year = g.y AND m.quarter = g.q
```

### C3 completeness — 2025Q1–2026Q3 每季都有營收與 EPS
```sql
SELECT y, q FROM generate_series(2025, 2026) AS y CROSS JOIN generate_series(1, 4) AS q
WHERE (y, q) <= (2026, 3)
  AND NOT EXISTS (SELECT 1 FROM pop.mops_quarterly_financials m WHERE m.stk_code = '2454' AND m.fiscal_year = y AND m.quarter = q
                  AND m.revenue_millions IS NOT NULL AND m.eps IS NOT NULL)
```

### C4 uniqueness — 同一季只出現在一個 as_of（I019 的失效模式）
```sql
SELECT fiscal_year, quarter, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.mops_quarterly_financials WHERE stk_code = '2454' AND fiscal_year IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

## 已知限制
- golden 是新聞轉載的法說會數字（科技新報，與優分析、永豐金報導一致）；前三季累計 EPS 47.94 只有一家來源、且同篇的季淨利與其他來源不一致，沒有放進 golden。
- C2 的 0.015 容許來自 I001 的單季化設計（累計相減）；Q034 的台積電 0.01 差可能也是這個原因（Q034 已知限制寫的是稀釋／基本，兩者在台積電無法區分）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
