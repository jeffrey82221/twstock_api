---
id: Q016
title: 2454、2882、1101 目前股價淨值比在近 18 個月的第幾百分位？估值收盤價和日 K 對得上嗎？
strategy: valuation          # §5.1 估值
status: draft
stocks: ["2454", "2882", "1101"]
window: {since: 2025-06-05, until: 2026-11-27}
pop_tables: [company_valuation, ohlcv_daily]
crawl:                       # 出題時（2026-11-27）1101、2882 只有 2026-06 起 6 個月估值、三檔都沒有日 K
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["2454", "2882", "1101"]}
    order_by: ["valuation_date"]
    since: 2025-06-05
    budget: 42               # TWSE BWIBBU；3 檔 × 18 個月 − 已有 12
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["2454", "2882", "1101"]}
    order_by: ["month_start_date"]
    since: 2025-06-01
    budget: 54               # TWSE STOCK_DAY；3 檔 × 18 個月（兩個 seed 合計 96，§8.3 TWSE 上限 120）
issues: []
last_run: null
---

## 決策情境
目前 PBR 低於自身近 18 個月第 20 百分位 → 「相對便宜」，列入觀察；高於第 80 百分位 → 不追價。
金融股（2882）用 PBR 比 PER 適合；C2 FAIL 時估值收盤價不可信。

## 決策 SQL
```sql
WITH v AS (
  SELECT stk_code, as_of, pbr FROM pop.company_valuation
  WHERE stk_code IN ('2454','2882','1101') AND as_of >= DATE '2025-06-05' AND pbr IS NOT NULL
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
  FROM unnest(ARRAY['2454','2882','1101']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-06-01', CURRENT_DATE - 4, INTERVAL '1 month') AS g
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
WHERE v.stk_code IN ('2454','2882','1101') AND v.as_of >= DATE '2025-06-05'
  AND abs(v.close_price - o.close) > 0.005 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 54）：
```sql
SELECT count(*) FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('2454','2882','1101') AND v.as_of >= DATE '2025-06-05' AND v.close_price IS NOT NULL
```

### C3 uniqueness — 同一檔同一個實際交易日只出現在一個 as_of
```sql
SELECT stk_code, data_date, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.company_valuation WHERE stk_code IN ('2454','2882','1101') AND data_date IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

### C4 precision — PER、PBR、殖利率到小數第二位，收盤價落在升降單位上
全表 1,684 列，PER／PBR／殖利率 0 違規（2026-11-27）；升降單位同 §7.1 的 precision 範例。
```sql
SELECT stk_code, as_of, close_price, per, pbr, dividend_yield_pct FROM pop.company_valuation
WHERE stk_code IN ('2454','2882','1101')
  AND (per * 100 <> round(per * 100) OR pbr * 100 <> round(pbr * 100) OR dividend_yield_pct * 100 <> round(dividend_yield_pct * 100)
       OR close_price % CASE WHEN close_price < 10 THEN 0.01 WHEN close_price < 50 THEN 0.05 WHEN close_price < 100 THEN 0.1
                             WHEN close_price < 500 THEN 0.5 WHEN close_price < 1000 THEN 1 ELSE 5 END <> 0)
```

### C5 timeliness — 實際資料日不晚於取樣日、落後 ≤ 12 天
```sql
SELECT stk_code, as_of, data_date FROM pop.company_valuation
WHERE stk_code IN ('2454','2882','1101') AND (data_date > as_of OR data_date < as_of - 12)
```

## 已知限制
- **還沒有合理性斷言**：想寫「同一財報季內，隱含每股淨值（收盤價 ÷ PBR）只差捨入」，但 `company_valuation` 沒有 BWIBBU 的「財報年/季」欄（I025），用公告期限推換季日會誤判提早公告的公司（全表 49／1,065 組）。I025 修好後補上，`issues` 不列 I025，因為現有斷言不依賴它。
- PBR 換季時會跳動（2882 2026-08 → 09：2.01 → 1.66，財報基準 115/1 → 115/2，TWSE 原始資料如此），百分位反映的是「股價相對最新淨值」，不是純價格。
- 18 個月窗口只有 18 個點，百分位粒度約 5.6 個百分點。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
