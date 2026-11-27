---
id: Q033
title: 上櫃 3264 欣銓 2026 年 1–10 月每個月營收都比去年同月高嗎？DB 的月營收與累計營收和公司公布的一樣嗎？
strategy: growth             # §5.1 成長（上櫃邊界）
status: passing
stocks: ["3264"]
window: {since: 2025-10-01, until: 2026-10-31}
pop_tables: [monthly_revenue_twse]
crawl: []                    # 出題時（2026-11-27）由 Q025 補過 2025/10–2026/10
issues: []
last_run: 2026-11-27
---

## 決策情境
2026 年 1–10 月每個月營收年增率都 > 0、最低也 > 15% → 「全年成長確立」；任一個月衰退 → 重新檢視。上櫃版的 Q021（上市 2330）。C1、C2 FAIL 時結論不可信。

## 決策 SQL
```sql
SELECT count(*) AS n_months, count(*) FILTER (WHERE latest_month_yoy_pct > 0) AS n_positive,
       round(min(latest_month_yoy_pct)::numeric, 2) AS min_yoy, round(sum(latest_month_value) / 1e8, 2) AS ytd_e8
FROM (SELECT DISTINCT latest_month_label, latest_month_value, latest_month_yoy_pct FROM pop.monthly_revenue_twse
      WHERE stk_code = '3264' AND latest_month_label BETWEEN '2026/01' AND '2026/10' AND latest_month_value IS NOT NULL) m
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — 單月營收 = 公司公告值（億元到小數第二位，誤差 ±0.005 億）
```sql
WITH golden(label, revenue_e8) AS (VALUES
  ('2025/10', 12.56),   -- 出處：科技新報 finance.technews.tw/2025/11/07/ardentec-3264-202510-financial-report/（10 月營收 12.56 億元），取得於 2026-11-27
  ('2026/09', 17.76),   -- 出處：科技新報 finance.technews.tw/2026/10/08/ardentec-3264-202609-financial-report/（9 月營收 17.76 億元），取得於 2026-11-27
  ('2026/10', 18.57)    -- 出處：科技新報 finance.technews.tw/2026/11/06/ardentec-3264-202610-financial-report/、Win 投資 winvest.tw/News/Detail/97518（10 月營收 18.57 億元），取得於 2026-11-27
)
SELECT g.label, g.revenue_e8 AS golden_e8, round(m.latest_month_value / 1e8, 4) AS db_e8
FROM golden g
LEFT JOIN (SELECT DISTINCT latest_month_label, latest_month_value FROM pop.monthly_revenue_twse WHERE stk_code = '3264') m
  ON m.latest_month_label = g.label
WHERE m.latest_month_value IS NULL OR abs(m.latest_month_value / 1e8 - g.revenue_e8) > 0.005
```
可比對列數（預期 3）：
```sql
WITH golden(label) AS (VALUES ('2025/10'), ('2026/09'), ('2026/10'))
SELECT count(*) FROM golden g
JOIN (SELECT DISTINCT latest_month_label FROM pop.monthly_revenue_twse WHERE stk_code = '3264' AND latest_month_value IS NOT NULL) m
  ON m.latest_month_label = g.label
```

### C2 accuracy — 累計營收（DB 各月加總）= 公司公告的累計值；比對範圍與 C1 不同（缺月或重複月會讓加總對不上）
```sql
WITH golden(through_label, ytd_e8) AS (VALUES
  ('2026/09', 138.54),   -- 出處：科技新報 9 月營收報導（累計前 9 月 138.54 億元），取得於 2026-11-27
  ('2026/10', 157.11)    -- 出處：科技新報 10 月營收報導（累計前 10 月 157.11 億元），取得於 2026-11-27
), m AS (
  SELECT DISTINCT latest_month_label, latest_month_value FROM pop.monthly_revenue_twse
  WHERE stk_code = '3264' AND latest_month_label BETWEEN '2026/01' AND '2026/12' AND latest_month_value IS NOT NULL
)
SELECT g.through_label, g.ytd_e8 AS golden_e8,
       round((SELECT sum(latest_month_value) FROM m WHERE m.latest_month_label <= g.through_label) / 1e8, 4) AS db_e8
FROM golden g
WHERE abs((SELECT sum(latest_month_value) FROM m WHERE m.latest_month_label <= g.through_label) / 1e8 - g.ytd_e8) > 0.01
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 2）：
```sql
SELECT count(*) FROM (VALUES ('2026/09'), ('2026/10')) g(through_label)
WHERE EXISTS (SELECT 1 FROM pop.monthly_revenue_twse WHERE stk_code = '3264' AND latest_month_label = g.through_label)
```

### C3 completeness — 2026/01–10 每個月都有一筆有值的營收
```sql
SELECT to_char(g, 'YYYY/MM') AS missing_label
FROM generate_series(DATE '2026-01-01', DATE '2026-10-01', INTERVAL '1 month') AS g
WHERE NOT EXISTS (SELECT 1 FROM pop.monthly_revenue_twse m
                  WHERE m.stk_code = '3264' AND m.latest_month_label = to_char(g, 'YYYY/MM') AND m.latest_month_value IS NOT NULL)
```

### C4 uniqueness — 同一營收月只出現在一個 as_of（I023 的失效模式，`otc` 頁）
```sql
SELECT latest_month_label, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.monthly_revenue_twse WHERE stk_code = '3264' AND latest_month_label IS NOT NULL
GROUP BY 1 HAVING count(*) > 1
```

## 已知限制
- golden 是新聞轉載的公司月營收公告（科技新報、Win 投資），不是公司官網原文；兩家數字一致。
- 只有 3264 一檔（上櫃版方法驗證）；上市版是 Q021（2330）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（4／4） | [run](../runs/2026-11-27_Q033.md) | 不需補爬；10 個月都年增 → 全年成長確立 |
