---
id: Q012
title: 2330、2317、2454 年度 EPS 是否連續成長？DB 的年度與季度 EPS 和公司公布的數字一樣嗎？
strategy: quality            # §5.1 體質
status: draft
stocks: ["2330", "2317", "2454"]
window: {since: 2022-01-01, until: 2025-12-31}
pop_tables: [financial_analysis, mops_quarterly_financials]
crawl:                       # 出題時（2026-11-27）2330 已齊；2317 全缺、2454 缺年度與 2023–2024Q2 季度
  - seed: financial_analysis_year_list
    filters: {stk_code: ["2317", "2454"]}
    order_by: ["year"]
    since: 2022
    budget: 8                # MOPS t51sb02；2 檔 × 2022–2025
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["2317", "2454"]}
    order_by: ["quarter_end"]
    since: 2023-03-31
    budget: 24               # MOPS t163sb06／04；2317 2023Q1–2026Q3 共 15 季、2454 缺的約 9 季（DESC 會先補 2026 的季度）
issues: []
last_run: null
---

## 決策情境
年度 EPS 連續 3 年（2023、2024、2025 對前一年）成長 → 列入「獲利成長股」名單；任一年衰退 → 不列入。
決策用的 EPS 必須通過 C1（和公司公布的數字一致），否則結論不可信。

## 決策 SQL
```sql
SELECT stk_code,
       max(eps) FILTER (WHERE year = 2022) AS eps_2022,
       max(eps) FILTER (WHERE year = 2023) AS eps_2023,
       max(eps) FILTER (WHERE year = 2024) AS eps_2024,
       max(eps) FILTER (WHERE year = 2025) AS eps_2025,
       bool_and(eps > prev_eps) FILTER (WHERE year BETWEEN 2023 AND 2025) AS grew_3y,
       count(*) FILTER (WHERE year BETWEEN 2022 AND 2025) AS n_years
FROM (SELECT stk_code, year, eps, lag(eps) OVER (PARTITION BY stk_code ORDER BY year) AS prev_eps
      FROM pop.financial_analysis WHERE stk_code IN ('2330','2317','2454') AND year BETWEEN 2022 AND 2025) x
GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — 年度 EPS（MOPS t51sb02）= 公司公布的全年 EPS（誤差 0.005 元）
```sql
WITH golden(stk_code, year, eps) AS (VALUES
  ('2330', 2022, 39.20),   -- 出處：台積電 2023 年報第 6 章財務概況（investor.tsmc.com/static/annualReports/2023/english/pdf/2023_tsmc_ar_e_ch6.pdf），取得於 2026-11-27
  ('2330', 2023, 32.34),   -- 出處：同上
  ('2330', 2024, 45.25),   -- 出處：台積電 2024Q4 財報新聞稿（pr.tsmc.com/english/news/3201），取得於 2026-11-27
  ('2317', 2023, 10.25),   -- 出處：鴻海 2023 年度股東報告書（財訊 moneyweekly.com.tw 轉載），取得於 2026-11-27
  ('2317', 2024, 11.01),   -- 出處：鴻海 2024 年度法說會（科技新報 finance.technews.tw/2025/03/14/hh-ai-server-3/），取得於 2026-11-27
  ('2317', 2025, 13.61),   -- 出處：鴻海 2025 年度財報公告（自由時報 ec.ltn.com.tw/article/breakingnews/5371923），取得於 2026-11-27
  ('2454', 2023, 48.51),   -- 出處：聯發科 2024Q4 法說會（科技新報 finance.technews.tw/2025/02/07/mediateks-eps-in-2024-will-reach-66-92-yuan/），取得於 2026-11-27
  ('2454', 2024, 66.92)    -- 出處：同上
)
SELECT g.stk_code, g.year, g.eps AS golden_eps, f.eps AS db_eps
FROM golden g LEFT JOIN pop.financial_analysis f ON f.stk_code = g.stk_code AND f.year = g.year
WHERE f.eps IS NULL OR abs(f.eps - g.eps) > 0.005
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 8）：
```sql
WITH golden(stk_code, year) AS (VALUES ('2330', 2022), ('2330', 2023), ('2330', 2024), ('2317', 2023),
                                       ('2317', 2024), ('2317', 2025), ('2454', 2023), ('2454', 2024))
SELECT count(*) FROM golden g JOIN pop.financial_analysis f ON f.stk_code = g.stk_code AND f.year = g.year
WHERE f.eps IS NOT NULL
```

### C2 accuracy — 季報（MOPS t163sb06／04）四季 EPS 合計 = 公司公布的全年 EPS（誤差 0.03 元：四季各自四捨五入到 0.01，合計最多差 0.02，再留 0.01）
比對範圍與 C1 不同（季報 adapter、不同 MOPS 頁面），所以同一個標籤用兩次。
```sql
WITH golden(stk_code, year, eps) AS (VALUES
  ('2330', 2022, 39.20), ('2330', 2023, 32.34), ('2330', 2024, 45.25),
  ('2317', 2023, 10.25), ('2317', 2024, 11.01), ('2317', 2025, 13.61),
  ('2454', 2023, 48.51), ('2454', 2024, 66.92)
), q AS (
  SELECT stk_code, fiscal_year, count(*) AS n_q, sum(eps) AS eps_4q
  FROM (SELECT DISTINCT stk_code, fiscal_year, quarter, eps FROM pop.mops_quarterly_financials
        WHERE stk_code IN ('2330','2317','2454') AND eps IS NOT NULL) d
  GROUP BY 1, 2
)
SELECT g.stk_code, g.year, g.eps AS golden_eps, q.n_q, q.eps_4q
FROM golden g LEFT JOIN q ON q.stk_code = g.stk_code AND q.fiscal_year = g.year
WHERE q.n_q IS DISTINCT FROM 4 OR abs(q.eps_4q - g.eps) > 0.03
```
可比對列數（預期 8）：
```sql
WITH golden(stk_code, year) AS (VALUES ('2330', 2022), ('2330', 2023), ('2330', 2024), ('2317', 2023),
                                       ('2317', 2024), ('2317', 2025), ('2454', 2023), ('2454', 2024))
SELECT count(*) FROM golden g
JOIN (SELECT stk_code, fiscal_year, count(DISTINCT quarter) AS n_q FROM pop.mops_quarterly_financials
      WHERE eps IS NOT NULL GROUP BY 1, 2) q ON q.stk_code = g.stk_code AND q.fiscal_year = g.year AND q.n_q = 4
```

### C3 completeness — 每檔 2022–2025 都有年度列，2023–2025 每年四季都有季報
```sql
WITH e AS (
  SELECT s.stk_code, y AS year, NULL::int AS quarter, 'annual' AS kind
  FROM unnest(ARRAY['2330','2317','2454']) AS s(stk_code) CROSS JOIN generate_series(2022, 2025) AS y
  UNION ALL
  SELECT s.stk_code, y, qq, 'quarterly'
  FROM unnest(ARRAY['2330','2317','2454']) AS s(stk_code) CROSS JOIN generate_series(2023, 2025) AS y
  CROSS JOIN generate_series(1, 4) AS qq
)
SELECT e.stk_code, e.year, e.quarter, e.kind FROM e
WHERE (e.kind = 'annual' AND NOT EXISTS (
         SELECT 1 FROM pop.financial_analysis f WHERE f.stk_code = e.stk_code AND f.year = e.year AND f.eps IS NOT NULL))
   OR (e.kind = 'quarterly' AND NOT EXISTS (
         SELECT 1 FROM pop.mops_quarterly_financials m
         WHERE m.stk_code = e.stk_code AND m.fiscal_year = e.year AND m.quarter = e.quarter AND m.eps IS NOT NULL))
```

### C4 uniqueness — 同一檔同一季只出現在一個 as_of（I019 的失效模式）
```sql
SELECT stk_code, fiscal_year, quarter, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.mops_quarterly_financials
WHERE stk_code IN ('2330','2317','2454') AND fiscal_year IS NOT NULL
GROUP BY 1, 2, 3 HAVING count(*) > 1
```

### C5 precision — 年度與季度 EPS 都是 0.01 元的整數倍（上游公布到小數第二位；出現更細的值代表被換算或推導過）
2026-11-27 對全表跑過：`financial_analysis` 273 列、`mops_quarterly_financials` 355 列，0 違規。
```sql
SELECT 'financial_analysis' AS src, stk_code, year AS period, eps FROM pop.financial_analysis
WHERE stk_code IN ('2330','2317','2454') AND eps * 100 <> round(eps * 100)
UNION ALL
SELECT 'mops_quarterly', stk_code, fiscal_year * 10 + quarter, eps FROM pop.mops_quarterly_financials
WHERE stk_code IN ('2330','2317','2454') AND eps * 100 <> round(eps * 100)
```

## 已知限制
- golden 值除了台積電年報與新聞稿以外，都是新聞轉載公司法說會或年報的數字（2026-11-27 以搜尋確認，兩家以上媒體一致），不是直接取自公司官網；換成年報 PDF 時更新註解。
- **2330 2025 沒有放進 golden**：法說會公布的 66.25 元是稀釋 EPS，DB（MOPS t51sb02、季報四季合計）都是 66.26，推測是基本 EPS。兩者定義不同，取得 2025 年報的基本 EPS 後再加入。其他年度基本與稀釋 EPS 相同。
- 2454 2025 的全年 EPS 出題時沒找到可信來源，沒有放進 golden；決策 SQL 仍會用 DB 的值，結論要註明 2454 2025 未驗證。
- 2330 2022–2024 出題時 DB 已有，C1、C2 對它們 0 違規（2026-11-27 試跑）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
