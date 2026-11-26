---
id: Q006
title: 上市大盤本益比目前在近 5 年第幾百分位？該不該降低持股？
strategy: market
status: passing
stocks: []
window: {since: 2021-11-05, until: 2026-11-26}
pop_tables: [market_valuation_summary, company_valuation, company_basic_info_list]
crawl: []                    # 出題時（2026-11-26）資料已齊：market_valuation_date_list 2017-05 起 115 個月 pending 0；C4 用的 2330 估值 2021-12 起已在 pop
issues: []
last_run: 2026-11-27
---

## 決策情境
大盤本益比高於自身近 5 年第 80 百分位、且大盤殖利率低於近 5 年第 20 百分位 → 不加碼、逐步降低持股；
本益比低於第 20 百分位 → 分批加碼。

## 決策 SQL
```sql
WITH m AS (
  SELECT valuation_date, market_per, market_dividend_yield_pct
  FROM pop.market_valuation_summary
  WHERE valuation_date >= (SELECT max(valuation_date) FROM pop.market_valuation_summary) - INTERVAL '5 years'
), l AS (
  SELECT * FROM m ORDER BY valuation_date DESC LIMIT 1
)
SELECT l.valuation_date, round(l.market_per, 2) AS market_per,
       round(100.0 * (SELECT count(*) FROM m WHERE m.market_per <= l.market_per)
             / NULLIF((SELECT count(market_per) FROM m), 0), 1) AS per_pctile,
       round(l.market_dividend_yield_pct, 2) AS yield_pct,
       round(100.0 * (SELECT count(*) FROM m WHERE m.market_dividend_yield_pct <= l.market_dividend_yield_pct)
             / NULLIF((SELECT count(market_dividend_yield_pct) FROM m), 0), 1) AS yield_pctile,
       (SELECT count(*) FROM m) AS n_months
FROM l
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 2017-05 起每月 5 日都有一筆大盤估值
```sql
SELECT g::date AS missing_valuation_date
FROM generate_series(DATE '2017-05-05', CURRENT_DATE, INTERVAL '1 month') AS g
WHERE NOT EXISTS (SELECT 1 FROM pop.market_valuation_summary m WHERE m.valuation_date = g::date)
```

### C2 field — 每個取樣日的大盤 PER、PBR、殖利率都有值
原本的 C2 比對 `market_per` 與 `Σ市值 ÷ Σ純益`，但 `market_per` 就是 adapter 用同一組加總算出來的（`app/valuation_source.py`），必定成立（README §7.2）。2026-11-27 改成只檢查欄位有值。
```sql
SELECT valuation_date, market_per, market_pbr, market_dividend_yield_pct
FROM pop.market_valuation_summary
WHERE market_per IS NULL OR market_pbr IS NULL OR market_dividend_yield_pct IS NULL
```

### C3 validity — 成分股計數自洽、指標在合理範圍
```sql
SELECT valuation_date, total_rows, constituent_count, excluded_count, excluded_no_price, excluded_no_shares,
       per_included, pbr_included, yield_included, market_per, market_pbr, market_dividend_yield_pct
FROM pop.market_valuation_summary
WHERE constituent_count + excluded_count <> total_rows
   OR excluded_no_price + excluded_no_shares <> excluded_count
   OR per_included > constituent_count OR pbr_included > constituent_count OR yield_included > constituent_count
   OR constituent_count NOT BETWEEN 800 AND 1300
   OR market_per NOT BETWEEN 5 AND 60 OR market_pbr NOT BETWEEN 0.5 AND 10
   OR market_dividend_yield_pct NOT BETWEEN 0.3 AND 10
```

### C4 unit — 2330 市值占大盤 15%–60%（收盤價取自 `company_valuation`，股本取自 `company_basic_info_list`）
2330 在 2021–2026 的大盤權重約 25%–45%。比值偏離一個數量級代表市值單位或股數估算錯。
```sql
SELECT m.valuation_date, v.close_price, b.paid_in_capital, m.total_market_cap,
       round(v.close_price * b.paid_in_capital / 10 / m.total_market_cap, 4) AS weight_2330
FROM pop.market_valuation_summary m
JOIN pop.company_valuation v ON v.stk_code = '2330' AND v.as_of = m.valuation_date
JOIN pop.company_basic_info_list b ON b.stk_code = '2330'
WHERE v.close_price * b.paid_in_capital / 10 / m.total_market_cap NOT BETWEEN 0.15 AND 0.60
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.market_valuation_summary m
JOIN pop.company_valuation v ON v.stk_code = '2330' AND v.as_of = m.valuation_date
```

### C5 timeliness — 實際資料日不晚於取樣日、落後不超過 12 天
```sql
SELECT valuation_date, as_of, data_date, as_of - data_date AS lag_days
FROM pop.market_valuation_summary
WHERE data_date > as_of OR data_date < as_of - 12 OR data_date IS NULL
```

## 已知限制
- 估流通股數 = `paid_in_capital / 10`，`paid_in_capital` 取自**今天**的公司基本資料（`app/main.py` 市場估值說明）。歷史月份的市值因此用今天的股本估算：股本有變動的公司（增資、減資、面額非 10 元的 KY 股）在早年月份的權重會失真。百分位只看大盤 PER 的相對位置，影響有限；C4 只用股本多年未變的 2330。
- 範圍只有上市（TWSE BWIBBU_d），不含上櫃。
- C5 容忍 12 天：春節最長休市。
- **沒有錯值防線**（README §5.2）：大盤 PER、PBR、殖利率都由 adapter 從同一份 BWIBBU 個股資料加總算出，資料庫裡沒有獨立來源可以互證。候選：TWSE 發行量加權股價指數（TAIEX）。本系統的歷史市值用固定股本估算，月與月之間的變化只反映股價，應該跟 TAIEX 的漲跌一致；但要先新增指數 endpoint 與 seed（程式改動，不在指令 7 範圍）。在那之前，run 的維度結論中準確性一律寫「未驗證」。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | PASS 5／5 | [run](../runs/2026-11-27_Q006.md) | 無需補爬；C2 改寫成 field（原本的互推必定成立）；準確性未驗證 |
