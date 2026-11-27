---
id: Q021
title: 2330 2026 年 1–10 月每個月營收都比去年同月高嗎？DB 的月營收與累計營收和台積電公告的一樣嗎？
strategy: growth             # §5.1 成長
status: draft
stocks: ["2330"]
window: {since: 2026-01-01, until: 2026-10-31}
pop_tables: [monthly_revenue_twse]
crawl: []                    # 出題時（2026-11-27）2330 2026/01–10 每月都已在 pop（as_of 2026-02-10 ～ 11-10）
issues: []
last_run: null
---

## 決策情境
2026 年 1–10 月每個月的營收年增率都 > 0，而且最低的一個月也 > 15% → 「全年成長確立」，續抱；任一個月衰退 → 重新檢視。
C1、C2 FAIL 時結論不可信。

## 決策 SQL
```sql
SELECT count(*) AS n_months,
       count(*) FILTER (WHERE latest_month_yoy_pct > 0) AS n_positive,
       round(min(latest_month_yoy_pct)::numeric, 2) AS min_yoy,
       round(sum(latest_month_value) / 1e8, 2) AS ytd_e8
FROM (SELECT DISTINCT latest_month_label, latest_month_value, latest_month_yoy_pct FROM pop.monthly_revenue_twse
      WHERE stk_code = '2330' AND latest_month_label BETWEEN '2026/01' AND '2026/10' AND latest_month_value IS NOT NULL) m
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — 單月營收 = 台積電公告值（誤差 = 公告精度的一半：億元到小數第二位 ±0.005 億，只到億元 ±0.5 億）
```sql
WITH golden(label, revenue_e8, tol_e8) AS (VALUES
  ('2026/05', 4169.75, 0.005),   -- 出處：經濟日報 money.udn.com/money/story/5607/9557696（台積電 5 月營收 4,169 億 7,500 萬元），取得於 2026-11-27
  ('2026/08', 5148.0,  0.5),     -- 出處：EBC ebc.com/zh/jinrong/311546.html（8 月合併營收新台幣 5148 億），取得於 2026-11-27
  ('2026/09', 5118.57, 0.005),   -- 出處：台積公司 2026 年 9 月營收報告（Yahoo 股市轉載公告），5,118 億 5,700 萬元，取得於 2026-11-27
  ('2026/10', 5868.90, 0.005)    -- 出處：時報資訊 chinatimes.com/realtimenews/20261110002885-260410、科技新報（10 月合併營收 5,868.90 億元），取得於 2026-11-27
)
SELECT g.label, g.revenue_e8 AS golden_e8, round(m.latest_month_value / 1e8, 4) AS db_e8
FROM golden g
LEFT JOIN (SELECT DISTINCT latest_month_label, latest_month_value FROM pop.monthly_revenue_twse WHERE stk_code = '2330') m
  ON m.latest_month_label = g.label
WHERE m.latest_month_value IS NULL OR abs(m.latest_month_value / 1e8 - g.revenue_e8) > g.tol_e8
```
可比對列數（預期 4）：
```sql
WITH golden(label) AS (VALUES ('2026/05'), ('2026/08'), ('2026/09'), ('2026/10'))
SELECT count(*) FROM golden g
JOIN (SELECT DISTINCT latest_month_label FROM pop.monthly_revenue_twse WHERE stk_code = '2330' AND latest_month_value IS NOT NULL) m
  ON m.latest_month_label = g.label
```

### C2 accuracy — 累計營收（DB 各月加總）= 台積電公告的累計值；比對範圍與 C1 不同（缺月或重複月都會讓加總對不上）
```sql
WITH golden(through_label, ytd_e8) AS (VALUES
  ('2026/05', 19618.04),   -- 出處：經濟日報同上（累計 1 至 5 月營收 1 兆 9,618 億 400 萬元），取得於 2026-11-27
  ('2026/10', 44856.17)    -- 出處：優分析 uanalyze.com.tw/articles/2557358846、時報資訊同上（前 10 月 4 兆 4,856.17 億元），取得於 2026-11-27
), m AS (
  SELECT DISTINCT latest_month_label, latest_month_value FROM pop.monthly_revenue_twse
  WHERE stk_code = '2330' AND latest_month_label BETWEEN '2026/01' AND '2026/12' AND latest_month_value IS NOT NULL
)
SELECT g.through_label, g.ytd_e8 AS golden_e8,
       round((SELECT sum(latest_month_value) FROM m WHERE m.latest_month_label <= g.through_label) / 1e8, 4) AS db_e8,
       (SELECT count(*) FROM m WHERE m.latest_month_label <= g.through_label) AS n_months
FROM golden g
WHERE abs((SELECT sum(latest_month_value) FROM m WHERE m.latest_month_label <= g.through_label) / 1e8 - g.ytd_e8) > 0.01
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 2）：
```sql
SELECT count(*) FROM (VALUES ('2026/05'), ('2026/10')) g(through_label)
WHERE EXISTS (SELECT 1 FROM pop.monthly_revenue_twse WHERE stk_code = '2330' AND latest_month_label = g.through_label)
```

### C3 completeness — 2026/01–10 每個月都有一筆有值的營收
```sql
SELECT to_char(g, 'YYYY/MM') AS missing_label
FROM generate_series(DATE '2026-01-01', DATE '2026-10-01', INTERVAL '1 month') AS g
WHERE NOT EXISTS (SELECT 1 FROM pop.monthly_revenue_twse m
                  WHERE m.stk_code = '2330' AND m.latest_month_label = to_char(g, 'YYYY/MM') AND m.latest_month_value IS NOT NULL)
```

### C4 uniqueness — 同一營收月只出現在一個 as_of（I023 的失效模式）
```sql
SELECT latest_month_label, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.monthly_revenue_twse WHERE stk_code = '2330' AND latest_month_label IS NOT NULL
GROUP BY 1 HAVING count(*) > 1
```

### C5 validity — 營收 > 0、標籤格式 YYYY/MM、年增率介於 −100% 與 10,000%
全表 508 列 0 違規（2026-11-27）。
```sql
SELECT stk_code, as_of, latest_month_label, latest_month_value, latest_month_yoy_pct FROM pop.monthly_revenue_twse
WHERE stk_code = '2330' AND latest_month_value IS NOT NULL
  AND (latest_month_value <= 0 OR latest_month_label !~ '^[12][0-9]{3}/(0[1-9]|1[0-2])$'
       OR latest_month_yoy_pct NOT BETWEEN -100 AND 10000)
```

## 已知限制
- golden 是新聞轉載的台積電月營收公告；4 月只找到摘要裡的「410,726 百萬元」，DB 是 410,725.118 百萬元，差 1 百萬元但來源不確定，沒有放進 golden。
- 8 月只找到「5148 億」，容許 ±0.5 億，抓不到小錯，只抓量級與張冠李戴。
- 只有一檔：golden 需要逐月找公告，先用台積電建立方法；其他公司的月營收由 Q013 C2（FinMind vs TWSE）做一致性。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
