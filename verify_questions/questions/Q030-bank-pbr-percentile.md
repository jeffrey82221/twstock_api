---
id: Q030
title: 銀行股 5880、2886、2892 目前股價淨值比在近 12 個月的第幾百分位？估值收盤價和日 K 對得上嗎？
strategy: valuation          # §5.1 估值（金融股邊界）
status: passing
stocks: ["5880", "2886", "2892"]
window: {since: 2025-12-05, until: 2026-11-27}
pop_tables: [company_valuation, ohlcv_daily]
crawl:                       # 出題時（2026-11-27）2892 只有 2 列估值、其他沒有；三檔都沒有日 K
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["5880", "2886", "2892"]}
    order_by: ["valuation_date"]
    since: 2025-12-05
    budget: 36               # TWSE BWIBBU；3 檔 × 12 個月
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["5880", "2886", "2892"]}
    order_by: ["month_start_date"]
    since: 2025-12-01
    budget: 36               # TWSE STOCK_DAY；3 檔 × 12 個月（合計 72，§8.3 上限 120）
issues: []
last_run: 2026-11-27
---

## 決策情境
目前 PBR 低於自身近 12 個月第 20 百分位 → 「相對便宜」，列入觀察；高於第 80 百分位 → 不追價。
銀行股用 PBR 比 PER 適合；C2 FAIL 時估值收盤價不可信。

## 決策 SQL
```sql
WITH v AS (
  SELECT stk_code, as_of, pbr FROM pop.company_valuation
  WHERE stk_code IN ('5880','2886','2892') AND as_of >= DATE '2025-12-05' AND pbr IS NOT NULL
), latest AS (
  SELECT DISTINCT ON (stk_code) stk_code, as_of, pbr FROM v ORDER BY stk_code, as_of DESC
)
SELECT l.stk_code, l.as_of, l.pbr,
       round(100.0 * (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code AND v.pbr <= l.pbr)
             / (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code), 1) AS pbr_pctile,
       (SELECT min(pbr) FROM v WHERE v.stk_code = l.stk_code) AS pbr_min,
       (SELECT max(pbr) FROM v WHERE v.stk_code = l.stk_code) AS pbr_max,
       (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code) AS n_months
FROM latest l ORDER BY l.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每檔每月 5 日都有一筆 PBR
```sql
WITH e AS (
  SELECT s.stk_code, (date_trunc('month', g) + INTERVAL '4 days')::date AS as_of
  FROM unnest(ARRAY['5880','2886','2892']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-01', CURRENT_DATE - 4, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of AS missing_as_of FROM e
WHERE NOT EXISTS (SELECT 1 FROM pop.company_valuation v
                  WHERE v.stk_code = e.stk_code AND v.as_of = e.as_of AND v.pbr IS NOT NULL)
```

### C2 cross-source — 估值收盤價（TWSE BWIBBU）= 同一交易日的日 K 收盤價（STOCK_DAY）（誤差 0.5%）
兩個不同的 TWSE endpoint 與 adapter；收盤價是 BWIBBU 直接給的，不是推導值。
```sql
SELECT v.stk_code, v.data_date, v.close_price, o.close
FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('5880','2886','2892') AND v.as_of >= DATE '2025-12-05'
  AND abs(v.close_price - o.close) > 0.005 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 36）：
```sql
SELECT count(*) FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('5880','2886','2892') AND v.as_of >= DATE '2025-12-05' AND v.close_price IS NOT NULL
```

### C3 uniqueness — 同一檔同一個實際交易日只出現在一個 as_of
```sql
SELECT stk_code, data_date, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.company_valuation WHERE stk_code IN ('5880','2886','2892') AND data_date IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

### C4 precision — PER、PBR、殖利率到小數第二位，收盤價落在升降單位上
升降單位同 §7.1 的 precision 範例。
```sql
SELECT stk_code, as_of, close_price, per, pbr, dividend_yield_pct FROM pop.company_valuation
WHERE stk_code IN ('5880','2886','2892')
  AND (per * 100 <> round(per * 100) OR pbr * 100 <> round(pbr * 100) OR dividend_yield_pct * 100 <> round(dividend_yield_pct * 100)
       OR close_price % CASE WHEN close_price < 10 THEN 0.01 WHEN close_price < 50 THEN 0.05 WHEN close_price < 100 THEN 0.1
                             WHEN close_price < 500 THEN 0.5 WHEN close_price < 1000 THEN 1 ELSE 5 END <> 0)
```

### C5 timeliness — 實際資料日不晚於取樣日、落後 ≤ 12 天
```sql
SELECT stk_code, as_of, data_date FROM pop.company_valuation
WHERE stk_code IN ('5880','2886','2892') AND (data_date > as_of OR data_date < as_of - 12)
```

## 已知限制
- 三檔都是以銀行為主的金控／銀行，避開壽險為主的 2882（[I019](../issues/I019-financial-q3-frozen-as-previous-quarter.md) 季報凍結、[I020](../issues/I020-2882-twse-per-eps-vs-mops.md) EPS 基準），也不用 PER（[I020](../issues/I020-2882-twse-per-eps-vs-mops.md) 的假說是 TWSE 對金控的 EPS 基準不同）。
- PBR 換季時會跳動（[Q016](Q016-valuation-pbr-percentile.md) 已知限制）；財報基準欄要等 [I025](../issues/I025-valuation-drops-report-period.md)。
- 12 個月窗口只有 12 個點，百分位粒度約 8.3 個百分點。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（5／5） | [run](../runs/2026-11-27_Q030.md) | 補爬 TWSE 72 列；三檔 PBR 第 100 百分位 → 不追價；官網抽查一致 |
