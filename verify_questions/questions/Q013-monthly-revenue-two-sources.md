---
id: Q013
title: 2330、2317、1402 月營收年增率是否連 3 個月為正且走高？FinMind 和 TWSE／MOPS 兩個來源一致嗎？
strategy: growth             # §5.1 成長
status: draft
stocks: ["2330", "2317", "1402"]
window: {since: 2026-05-01, until: 2026-11-27}
pop_tables: [monthly_revenue, monthly_revenue_twse]
crawl:                       # 出題時（2026-11-27）FinMind 只有 2330 as_of 2026-11-01 一列；TWSE 2330、1402 有近幾個月
  - seed: financial_month_list
    filters: {stk_code: ["2330", "2317", "1402"]}
    order_by: ["month_start_date"]
    since: 2026-06-01
    budget: 18               # FinMind；3 檔 × 2026-06-01 ～ 11-01（§8.3 上限 20，先看 crawl_status 的 FinMind 用量）
  - seed: financial_month_twse_list
    filters: {stk_code: ["2330", "2317", "1402"]}
    order_by: ["as_of_date"]
    since: 2026-06-10
    budget: 18               # TWSE／MOPS t21sc03；3 檔 × 2026-06-10 ～ 11-10
issues: []
last_run: null
---

## 決策情境
TWSE 月營收年增率最近 3 個月都 > 0，且最近 3 個月平均高於前 3 個月平均 → 「營收動能轉強」，列入觀察；
最近 3 個月有任一個月 < 0 → 不列入。C2（兩源一致）FAIL 時結論不可信。

## 決策 SQL
```sql
WITH t AS (
  SELECT stk_code, as_of, latest_month_label, latest_month_yoy_pct,
         row_number() OVER (PARTITION BY stk_code ORDER BY as_of DESC) AS rn
  FROM pop.monthly_revenue_twse
  WHERE stk_code IN ('2330','2317','1402') AND as_of >= DATE '2026-06-10' AND latest_month_value IS NOT NULL
)
SELECT stk_code,
       string_agg(latest_month_label || ' ' || round(latest_month_yoy_pct::numeric, 1), '、' ORDER BY as_of) AS months,
       bool_and(latest_month_yoy_pct > 0) FILTER (WHERE rn <= 3) AS last3_positive,
       round(avg(latest_month_yoy_pct) FILTER (WHERE rn <= 3)::numeric, 2) AS avg_last3,
       round(avg(latest_month_yoy_pct) FILTER (WHERE rn BETWEEN 4 AND 6)::numeric, 2) AS avg_prev3,
       count(*) AS n_months
FROM t GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 兩個來源在窗口內每個取樣日都有一列有值（TWSE 每月 10 日、FinMind 每月 1 日）
```sql
WITH e AS (
  SELECT s.stk_code, 'twse' AS src, (g + INTERVAL '9 days')::date AS as_of
  FROM unnest(ARRAY['2330','2317','1402']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2026-06-01', CURRENT_DATE - 9, INTERVAL '1 month') AS g
  UNION ALL
  SELECT s.stk_code, 'finmind', g::date
  FROM unnest(ARRAY['2330','2317','1402']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2026-06-01', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.src, e.as_of AS missing_as_of FROM e
WHERE (e.src = 'twse' AND NOT EXISTS (SELECT 1 FROM pop.monthly_revenue_twse t
         WHERE t.stk_code = e.stk_code AND t.as_of = e.as_of AND t.latest_month_value IS NOT NULL))
   OR (e.src = 'finmind' AND NOT EXISTS (SELECT 1 FROM pop.monthly_revenue f
         WHERE f.stk_code = e.stk_code AND f.as_of = e.as_of AND f.latest_month_value IS NOT NULL))
```

### C2 cross-source — 同一營收月，FinMind 月營收 = TWSE／MOPS 月營收（誤差 0.1%；兩邊都是公司申報的同一個數字，單位都換算成元）
兩邊是不同的上游（FinMind API vs TWSE OpenAPI＋MOPS t21sc03）、不同的 adapter，不是推導值。
```sql
SELECT f.stk_code, f.latest_month_label, f.latest_month_value AS finmind, t.latest_month_value AS twse,
       round(f.latest_month_value / NULLIF(t.latest_month_value, 0), 5) AS ratio
FROM (SELECT DISTINCT stk_code, latest_month_label, latest_month_value FROM pop.monthly_revenue
      WHERE stk_code IN ('2330','2317','1402') AND latest_month_value IS NOT NULL) f
JOIN (SELECT DISTINCT stk_code, latest_month_label, latest_month_value FROM pop.monthly_revenue_twse
      WHERE stk_code IN ('2330','2317','1402') AND latest_month_value IS NOT NULL) t
  ON t.stk_code = f.stk_code AND t.latest_month_label = f.latest_month_label
WHERE abs(f.latest_month_value - t.latest_month_value) > 0.001 * t.latest_month_value
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 15 = 3 檔 × 2026/05–09，FinMind 標籤 2026/04–09、TWSE 2026/05–10 的交集）：
```sql
SELECT count(*) FROM
  (SELECT DISTINCT stk_code, latest_month_label FROM pop.monthly_revenue
   WHERE stk_code IN ('2330','2317','1402') AND latest_month_value IS NOT NULL AND as_of >= DATE '2026-06-01') f
JOIN (SELECT DISTINCT stk_code, latest_month_label FROM pop.monthly_revenue_twse
      WHERE stk_code IN ('2330','2317','1402') AND latest_month_value IS NOT NULL) t USING (stk_code, latest_month_label)
```

### C3 uniqueness — 兩個來源都是同一檔同一營收月只有一列（I023 的失效模式：截斷頁讓相鄰取樣日回同一個月）
```sql
SELECT 'twse' AS src, stk_code, latest_month_label, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.monthly_revenue_twse WHERE stk_code IN ('2330','2317','1402') AND latest_month_label IS NOT NULL
GROUP BY 2, 3 HAVING count(*) > 1
UNION ALL
SELECT 'finmind', stk_code, latest_month_label, array_agg(as_of ORDER BY as_of)
FROM pop.monthly_revenue WHERE stk_code IN ('2330','2317','1402') AND latest_month_label IS NOT NULL
GROUP BY 2, 3 HAVING count(*) > 1
```

### C4 point-in-time — 取樣日看到的營收月，公告期限（次月 10 日）不晚於取樣日
FinMind 的取樣日是每月 1 日，應該看到上上個月；TWSE 是 10 日，看到上個月。
```sql
SELECT src, stk_code, as_of, latest_month_label, deadline FROM (
  SELECT 'finmind' AS src, stk_code, as_of, latest_month_label,
         (to_date(latest_month_label, 'YYYY/MM') + INTERVAL '1 month 9 days')::date AS deadline
  FROM pop.monthly_revenue WHERE stk_code IN ('2330','2317','1402') AND latest_month_label IS NOT NULL
  UNION ALL
  SELECT 'twse', stk_code, as_of, latest_month_label,
         (to_date(latest_month_label, 'YYYY/MM') + INTERVAL '1 month 9 days')::date
  FROM pop.monthly_revenue_twse WHERE stk_code IN ('2330','2317','1402') AND latest_month_label IS NOT NULL
) x WHERE deadline > as_of
```

### C5 precision — 月營收是 1,000 元的整數倍（上游公布單位是仟元；不是整數倍代表換算錯或混到別的欄位）
2026-11-27 對全表跑過：`monthly_revenue_twse` 499 列、`monthly_revenue` 1 列，0 違規。
```sql
SELECT 'twse' AS src, stk_code, as_of, latest_month_value FROM pop.monthly_revenue_twse
WHERE stk_code IN ('2330','2317','1402') AND latest_month_value % 1000 <> 0
UNION ALL
SELECT 'finmind', stk_code, as_of, latest_month_value FROM pop.monthly_revenue
WHERE stk_code IN ('2330','2317','1402') AND latest_month_value % 1000 <> 0
```

## 已知限制
- 股票刻意避開 I023 影響的 2308、2882、3045（MOPS 115/09 截斷頁之後的公司）；I023 修好後，C3 可以擴大到全表當回歸。2330、2317 也在截斷點之後：**本題補爬 TWSE 前先用 `upstream_peek.py t21sc03 sii:115:9` 確認頁面完整**，否則 2317 as_of 2026-10-10、11-10 會凍結成錯的列。2026-11-27 18:58 上游已恢復，第 6 輪重啟 API 清掉記憶體快取（I023）。
- FinMind 與 TWSE 的取樣日不同（1 日 vs 10 日），C2 用營收月對齊，不用 as_of。
- FinMind 額度與 cron 共用（§8.3 規則 6），補爬前先看 `crawl_status(lookback_minutes=60)`。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
