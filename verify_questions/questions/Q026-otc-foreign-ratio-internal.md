---
id: Q026
title: 上櫃 5483、3105、3264 外資持股比近 3 個月是否上升、法人是否站在買方？TPEx 的外資持股股數、比率、上限彼此對得上嗎？
strategy: flow               # §5.1 籌碼（上櫃邊界）
status: draft
stocks: ["5483", "3105", "3264"]
window: {since: 2025-12-05, until: 2026-11-27}
pop_tables: [foreign_ownership, institutional_net_buy_sell]
crawl:                       # 出題時（2026-11-27）三檔在兩張表都沒有任何列
  - seed: foreign_ownership_monthly_list
    filters: {stk_code: ["5483", "3105", "3264"]}
    order_by: ["month_start_date"]
    since: 2025-12-05
    budget: 36               # TPEx qfii（I006）；3 檔 × 12 個月
  - seed: institutional_monthly_list
    filters: {stk_code: ["5483", "3105", "3264"]}
    order_by: ["month_start_date"]
    since: 2025-12-05
    budget: 36               # TPEx 三大法人；3 檔 × 12 個月（合計 72，§8.3 上限 120）
issues: []
last_run: null
---

## 決策情境
最近 3 個取樣月的外資持股比逐月上升，而且同期三大法人合計（取樣日當天）至少 2 次買超 → 「籌碼轉強」，列入觀察；
外資持股比逐月下降 → 「籌碼轉弱」，不加碼。C2 FAIL 時持股比不可信。

## 決策 SQL
```sql
WITH f AS (
  SELECT stk_code, as_of, foreign_investment_ratio_pct AS r,
         row_number() OVER (PARTITION BY stk_code ORDER BY as_of DESC) AS rn
  FROM pop.foreign_ownership WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
), i AS (
  SELECT stk_code, as_of, total_institutional_net_buy_sell AS net,
         row_number() OVER (PARTITION BY stk_code ORDER BY as_of DESC) AS rn
  FROM pop.institutional_net_buy_sell WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
)
SELECT f.stk_code,
       max(r) FILTER (WHERE rn = 4) AS ratio_m4, max(r) FILTER (WHERE rn = 3) AS ratio_m3,
       max(r) FILTER (WHERE rn = 2) AS ratio_m2, max(r) FILTER (WHERE rn = 1) AS ratio_latest,
       (max(r) FILTER (WHERE rn = 1) > max(r) FILTER (WHERE rn = 2)
        AND max(r) FILTER (WHERE rn = 2) > max(r) FILTER (WHERE rn = 3)
        AND max(r) FILTER (WHERE rn = 3) > max(r) FILTER (WHERE rn = 4)) AS rising_3m,
       (SELECT count(*) FROM i WHERE i.stk_code = f.stk_code AND i.rn <= 3 AND i.net > 0) AS inst_buy_days_3m
FROM f GROUP BY f.stk_code ORDER BY f.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 兩張表在窗口內每月 5 日的取樣都有一列有值
```sql
WITH e AS (
  SELECT s.stk_code, g::date AS as_of
  FROM unnest(ARRAY['5483','3105','3264']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-05', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of,
       EXISTS (SELECT 1 FROM pop.foreign_ownership f WHERE f.stk_code = e.stk_code AND f.as_of = e.as_of
               AND f.foreign_investment_ratio_pct IS NOT NULL) AS has_foreign,
       EXISTS (SELECT 1 FROM pop.institutional_net_buy_sell i WHERE i.stk_code = e.stk_code AND i.as_of = e.as_of
               AND i.total_institutional_net_buy_sell IS NOT NULL) AS has_inst
FROM e
WHERE NOT EXISTS (SELECT 1 FROM pop.foreign_ownership f WHERE f.stk_code = e.stk_code AND f.as_of = e.as_of
                  AND f.foreign_investment_ratio_pct IS NOT NULL)
   OR NOT EXISTS (SELECT 1 FROM pop.institutional_net_buy_sell i WHERE i.stk_code = e.stk_code AND i.as_of = e.as_of
                  AND i.total_institutional_net_buy_sell IS NOT NULL)
```

### C2 internal — 外資持股比率 = 持有股數 ÷ 發行股數，無條件捨去到 0.01%（上游三個欄位都是 TPEx qfii 直接給的，adapter 不推導）
沿用 Q015 C2 的捨去規則（TWSE 是捨去）。TPEx 的捨入方式沒有另外確認：出題時全表有 TPEx 的 6488 15 列，用捨去規則 0 違規；本題三檔補爬後若違規，先查 TPEx 是否改成四捨五入（L7），不要直接開 issue。
```sql
SELECT stk_code, as_of, foreign_investment_shares, issued_shares, foreign_investment_ratio_pct,
       round(100.0 * foreign_investment_shares / issued_shares, 4) AS calc_pct
FROM pop.foreign_ownership
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05' AND issued_shares > 0
  AND 100.0 * foreign_investment_shares / issued_shares - foreign_investment_ratio_pct NOT BETWEEN -0.0001 AND 0.0101
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 36）：
```sql
SELECT count(*) FROM pop.foreign_ownership
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05' AND issued_shares > 0
  AND foreign_investment_ratio_pct IS NOT NULL
```

### C3 internal — 投資上限比率 = 持股比率 + 尚可投資比率（兩個都捨去到 0.01%，合計最多差 0.02）；比對範圍與 C2 不同
上限比率為 0 的列排除（Q015 C3：TWSE 2004 年的 L0 怪值）。
```sql
SELECT stk_code, as_of, foreign_investment_limit_ratio_pct AS lim, foreign_investment_ratio_pct AS held,
       foreign_investment_available_ratio_pct AS avail,
       round(foreign_investment_limit_ratio_pct - foreign_investment_ratio_pct - foreign_investment_available_ratio_pct, 4) AS gap
FROM pop.foreign_ownership
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05' AND foreign_investment_limit_ratio_pct > 0
  AND foreign_investment_limit_ratio_pct - foreign_investment_ratio_pct - foreign_investment_available_ratio_pct
      NOT BETWEEN -0.0001 AND 0.0201
```
可比對列數（預期 36）：
```sql
SELECT count(*) FROM pop.foreign_ownership
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05' AND foreign_investment_limit_ratio_pct > 0
```

### C4 uniqueness — 同一檔同一個實際交易日只出現一次（兩張表；相鄰取樣日都回溯到同一天時會重複）
```sql
SELECT 'foreign' AS src, stk_code, data_date AS d, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.foreign_ownership WHERE stk_code IN ('5483','3105','3264') AND data_date IS NOT NULL
GROUP BY 2, 3 HAVING count(*) > 1
UNION ALL
SELECT 'inst', stk_code, trade_date, array_agg(as_of ORDER BY as_of)
FROM pop.institutional_net_buy_sell WHERE stk_code IN ('5483','3105','3264') AND trade_date IS NOT NULL
GROUP BY 2, 3 HAVING count(*) > 1
```

### C5 timeliness — 實際交易日不晚於取樣日、也不早於取樣日 12 天以上（春節最長休市，同 Q001 C5）
全表（外資 88 列、法人 506 列）0 違規（2026-11-27）。
```sql
SELECT 'foreign' AS src, stk_code, as_of, data_date AS d FROM pop.foreign_ownership
WHERE stk_code IN ('5483','3105','3264') AND (data_date > as_of OR data_date < as_of - 12)
UNION ALL
SELECT 'inst', stk_code, as_of, trade_date FROM pop.institutional_net_buy_sell
WHERE stk_code IN ('5483','3105','3264') AND (trade_date > as_of OR trade_date < as_of - 12)
```

### C6 precision — 股數與買賣超是整數股，比率到小數第二位
全表 0 違規（2026-11-27）。
```sql
SELECT 'foreign' AS src, stk_code, as_of, foreign_investment_shares AS v FROM pop.foreign_ownership
WHERE stk_code IN ('5483','3105','3264')
  AND (foreign_investment_shares <> round(foreign_investment_shares) OR issued_shares <> round(issued_shares)
       OR foreign_investment_ratio_pct * 100 <> round(foreign_investment_ratio_pct * 100))
UNION ALL
SELECT 'inst', stk_code, as_of, total_institutional_net_buy_sell FROM pop.institutional_net_buy_sell
WHERE stk_code IN ('5483','3105','3264')
  AND (total_institutional_net_buy_sell <> round(total_institutional_net_buy_sell)
       OR foreign_investors_net_buy_sell <> round(foreign_investors_net_buy_sell))
```

## 已知限制
- 法人買賣超只有取樣日（每月 5 日回溯到交易日）當天一筆，不是月合計；「3 個月內 2 次買超」是很粗的訊號。
- TPEx qfii 沒有「陸資法令投資上限比率」欄（`app/foreign_ownership_source.py` 221 行設為 None），C2、C3 只用外資欄位。
- 與 Q015 的差別只有市場：上櫃走 TPEx qfii（外資持股）與 TPEx 三大法人（`app/institutional_source.py`），兩個 adapter 都只有 6488 驗過。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
