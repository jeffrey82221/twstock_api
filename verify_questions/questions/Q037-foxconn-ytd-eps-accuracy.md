---
id: Q037
title: 2317 鴻海 2026 年前三季 EPS 是否比去年同期成長 20% 以上？DB 的季營收、季 EPS 和法說會公布的一樣嗎？
strategy: growth             # §5.1 成長
status: passing
stocks: ["2317"]
window: {since: 2025-01-01, until: 2026-09-30}
pop_tables: [mops_quarterly_financials]
crawl: []                    # 出題時（2026-11-28）2317 2023Q1–2026Q3 由 Q012 補過
issues: []
last_run: 2026-11-28
---

## 決策情境
2026 年前三季 EPS 合計比 2025 年同期成長 > 20%，且 Q3 營收年增 > 30% → 「成長加速」，續抱；前三季 EPS 衰退 → 重新檢視。
C1、C2 FAIL 時結論不可信。

## 決策 SQL
```sql
WITH q AS (SELECT DISTINCT fiscal_year, quarter, revenue_millions, eps FROM pop.mops_quarterly_financials WHERE stk_code = '2317')
SELECT (SELECT sum(eps) FROM q WHERE fiscal_year = 2025 AND quarter <= 3) AS eps_ytd_2025,
       (SELECT sum(eps) FROM q WHERE fiscal_year = 2026 AND quarter <= 3) AS eps_ytd_2026,
       round(100.0 * ((SELECT sum(eps) FROM q WHERE fiscal_year = 2026 AND quarter <= 3)
                     / (SELECT sum(eps) FROM q WHERE fiscal_year = 2025 AND quarter <= 3) - 1), 1) AS eps_ytd_growth_pct,
       round(100.0 * ((SELECT revenue_millions FROM q WHERE fiscal_year = 2026 AND quarter = 3)
                     / (SELECT revenue_millions FROM q WHERE fiscal_year = 2025 AND quarter = 3) - 1), 1) AS q3_revenue_yoy_pct
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — 2026Q3 季營收（百萬元）與季 EPS = 法說會公布值（營收精度億元 ±50 百萬，EPS ±0.005）
```sql
WITH golden(revenue_millions, eps) AS (VALUES
  (3015600, 5.51)   -- 出處：鴻海 2026Q3 法說會（優分析 uanalyze.com.tw/articles/3008659196：營收 3 兆 156 億元、EPS 5.51 元），取得於 2026-11-28
), m AS (SELECT DISTINCT revenue_millions, eps FROM pop.mops_quarterly_financials WHERE stk_code = '2317' AND fiscal_year = 2026 AND quarter = 3)
SELECT g.revenue_millions AS golden_rev, m.revenue_millions AS db_rev, g.eps AS golden_eps, m.eps AS db_eps
FROM golden g LEFT JOIN m ON TRUE
WHERE m.eps IS NULL OR abs(m.revenue_millions - g.revenue_millions) > 50 OR abs(m.eps - g.eps) > 0.005
```
可比對列數（預期 1）：
```sql
SELECT count(*) FROM (SELECT DISTINCT revenue_millions, eps FROM pop.mops_quarterly_financials
                      WHERE stk_code = '2317' AND fiscal_year = 2026 AND quarter = 3 AND eps IS NOT NULL) m
```

### C2 accuracy — 2026 年前三季 EPS（三季加總）= 法說會公布的前三季累計 13.35 元（誤差 0.02：三季各自四捨五入）；比對範圍與 C1 不同
```sql
WITH m AS (SELECT DISTINCT quarter, eps FROM pop.mops_quarterly_financials WHERE stk_code = '2317' AND fiscal_year = 2026 AND quarter <= 3)
SELECT 13.35 AS golden_ytd,   -- 出處：優分析同 C1（前三季每股賺 13.35 元），取得於 2026-11-28
       (SELECT sum(eps) FROM m) AS db_ytd, (SELECT count(*) FROM m) AS n_quarters
WHERE (SELECT count(*) FROM m) <> 3 OR abs((SELECT sum(eps) FROM m) - 13.35) > 0.02
```
可比對列數（預期 3 季）：
```sql
SELECT count(DISTINCT quarter) FROM pop.mops_quarterly_financials WHERE stk_code = '2317' AND fiscal_year = 2026 AND quarter <= 3 AND eps IS NOT NULL
```

### C3 completeness — 2025Q1–2026Q3 每季都有營收與 EPS
```sql
SELECT y, q FROM generate_series(2025, 2026) AS y CROSS JOIN generate_series(1, 4) AS q
WHERE (y, q) <= (2026, 3)
  AND NOT EXISTS (SELECT 1 FROM pop.mops_quarterly_financials m WHERE m.stk_code = '2317' AND m.fiscal_year = y AND m.quarter = q
                  AND m.revenue_millions IS NOT NULL AND m.eps IS NOT NULL)
```

### C4 uniqueness — 同一季只出現在一個 as_of（I019 的失效模式）
```sql
SELECT fiscal_year, quarter, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.mops_quarterly_financials WHERE stk_code = '2317' AND fiscal_year IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

## 已知限制
- golden 是新聞轉載的法說會數字（優分析，與科技新報、數位時代報導一致），不是公司新聞稿原文。
- Q1、Q2 單季 EPS 沒有找到直接公布的數字（只能由前三季累計反推），所以 C1 只放 Q3；Q1、Q2 由 C2 的累計值間接驗證。
- 2025 全年 EPS 13.61 由 Q012 C1／C2 驗過（四季加總 3.03＋3.20＋4.15＋3.23 = 13.61）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-28 | passing（4／4） | [run](../runs/2026-11-28_Q037.md) | 不需補爬；前三季 EPS 年增 28.6% → 成長加速 |
