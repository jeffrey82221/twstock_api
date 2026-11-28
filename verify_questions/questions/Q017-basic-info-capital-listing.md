---
id: Q017
title: 2330、2454、6488、7769、2317 掛牌多久、資本多大、屬於哪些產業鏈？基本資料的實收資本額和外資持股表的發行股數對得上嗎？
strategy: industry           # §5.1 產業
status: passing
stocks: ["2330", "2454", "6488", "7769", "2317"]
window: {since: 2025-11-01, until: 2026-11-27}
pop_tables: [company_basic_info_list, chain_info_list, foreign_ownership, ohlcv_daily]
crawl:                       # 出題時（2026-11-27）7769 沒有外資持股與日 K；其他四檔都有最新一期外資持股
  - seed: foreign_ownership_monthly_list
    filters: {stk_code: ["7769"]}
    order_by: ["month_start_date"]
    since: 2026-11-05
    budget: 1                # TWSE MI_QFIIS；只要最新一期（C2）
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["7769"]}
    order_by: ["month_start_date"]
    since: 2025-11-01
    budget: 13               # TWSE STOCK_DAY；掛牌月 2025-11 起（C5 檢查掛牌前沒有價格）
issues: []
last_run: 2026-11-27
---

## 決策情境
這題是給其他題目用的「資料夠不夠」判斷：掛牌未滿 2 年的股票（7769）→ 估值百分位、3 年成長這類需要長歷史的決策一律不適用，結論要註明；
實收資本額 < 100 億元 → 標記「中小型」，波動度門檻（[Q011](Q011-technical-ma60-volatility.md)）要放寬解讀。C2 FAIL 時資本額不可信。

## 決策 SQL
```sql
SELECT b.stk_code, b.short_name, b.market, b.industry_name, b.listing_date,
       round(((now() AT TIME ZONE 'Asia/Taipei')::date - b.listing_date) / 365.25, 1) AS listed_years,
       round(b.paid_in_capital / 1e8, 1) AS paid_in_capital_e8,
       (SELECT count(DISTINCT ic_code) FROM pop.chain_info_list c WHERE c.stk_code = b.stk_code) AS n_chains,
       ((now() AT TIME ZONE 'Asia/Taipei')::date - b.listing_date) < 730 AS short_history,
       b.paid_in_capital < 1e10 AS small_cap
FROM pop.company_basic_info_list b
WHERE b.stk_code IN ('2330','2454','6488','7769','2317') ORDER BY b.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 field — 五檔的基本資料關鍵欄位都有值，而且都屬於至少一條產業鏈
```sql
SELECT s.stk_code, b.listing_date, b.market, b.industry_code, b.paid_in_capital,
       EXISTS (SELECT 1 FROM pop.chain_info_list c WHERE c.stk_code = s.stk_code) AS in_chain
FROM unnest(ARRAY['2330','2454','6488','7769','2317']) AS s(stk_code)
LEFT JOIN pop.company_basic_info_list b ON b.stk_code = s.stk_code
WHERE b.stk_code IS NULL OR b.listing_date IS NULL OR b.market IS NULL OR b.industry_code IS NULL
   OR b.paid_in_capital IS NULL
   OR NOT EXISTS (SELECT 1 FROM pop.chain_info_list c WHERE c.stk_code = s.stk_code)
```

### C2 cross-source — 實收資本額（公司基本資料）= 最新一期外資持股表的發行股數 × 面額 10 元（誤差 0.5%）
兩個不同來源：基本資料（TWSE／TPEx OpenAPI，`company_list` → `raw_company_info`）vs MI_QFIIS／TPEx qfii 的發行股數。抓單位錯（千元 vs 元）與張冠李戴。0.5% 容許基本資料與外資持股的快照日不同時，可轉債轉換造成的股本小幅變動（2317 實測 0.34%）。
```sql
WITH f AS (
  SELECT DISTINCT ON (stk_code) stk_code, data_date, issued_shares FROM pop.foreign_ownership
  WHERE stk_code IN ('2330','2454','6488','7769','2317') AND issued_shares > 0 ORDER BY stk_code, as_of DESC
)
SELECT b.stk_code, b.paid_in_capital, f.issued_shares, f.data_date,
       round(b.paid_in_capital / (f.issued_shares * 10), 4) AS ratio
FROM pop.company_basic_info_list b JOIN f USING (stk_code)
WHERE abs(b.paid_in_capital / (f.issued_shares * 10) - 1) > 0.005
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 5）：
```sql
SELECT count(DISTINCT b.stk_code) FROM pop.company_basic_info_list b
JOIN pop.foreign_ownership f ON f.stk_code = b.stk_code AND f.issued_shares > 0
WHERE b.stk_code IN ('2330','2454','6488','7769','2317')
```

### C3 uniqueness — 產業鏈表同一家公司在同一個子鏈只出現一次（實體鍵：產業鏈、上中下游、主鏈、子鏈、代號）
同一家公司屬於多個子鏈是合法的（2330 在半導體鏈多個子鏈），所以鍵要含子鏈；全表 6,889 列用這個鍵 0 重複，只用（產業鏈、上中下游、代號）會誤報 2,275 列（2026-11-27）。
```sql
SELECT ic_code, segment_key, top_code, sub_code, stk_code, count(*) AS n
FROM pop.chain_info_list WHERE stk_code IN ('2330','2454','6488','7769','2317')
GROUP BY 1, 2, 3, 4, 5 HAVING count(*) > 1
```

### C4 validity — 代號格式、市場別只有上市／上櫃、掛牌日介於 1962-02-09（第一家掛牌）與今天之間、資本額 > 0
全表 872 列 0 違規（2026-11-27）。
```sql
SELECT stk_code, market, listing_date, paid_in_capital FROM pop.company_basic_info_list
WHERE stk_code IN ('2330','2454','6488','7769','2317')
  AND (stk_code !~ '^[0-9]{4}[0-9A-Z]{0,2}$' OR market NOT IN ('上市', '上櫃')
       OR listing_date < DATE '1962-02-09' OR listing_date > (now() AT TIME ZONE 'Asia/Taipei')::date
       OR paid_in_capital <= 0)
```

### C5 reasonableness — 設立日不晚於掛牌日，日 K 的第一筆價格不早於掛牌日
全表 0 違規（2026-11-27）。抓掛牌日與設立日欄位對調，以及日 K 混入別檔（掛牌前就有價格）。
```sql
SELECT b.stk_code, b.incorporation_date, b.listing_date,
       (SELECT min(o.trade_date) FROM pop.ohlcv_daily o WHERE o.stk_code = b.stk_code AND o.close IS NOT NULL) AS first_trade
FROM pop.company_basic_info_list b
WHERE b.stk_code IN ('2330','2454','6488','7769','2317')
  AND (b.incorporation_date > b.listing_date
       OR (SELECT min(o.trade_date) FROM pop.ohlcv_daily o WHERE o.stk_code = b.stk_code AND o.close IS NOT NULL) < b.listing_date)
```

## 已知限制
- C2 不適用有特別股的公司：MI_QFIIS 的發行股數只算普通股。1101 的實收資本額 77,231,817,420 = (7,523,181,742 普通股 ＋ 200,000,000) × 10，差額剛好 2 億股；2882 差 10.45%。本題五檔都沒有特別股。
- 掛牌日沒有放 golden：出題時只找得到 7769 的報導（工商時報，2025-11-27 轉上市），其他四檔沒找到可引用的來源，C4 只做範圍檢查。
- 6488 在 2015-09-25 上櫃，日 K 走 TPEx（[I005](../issues/I005-otc-valuation-missing.md)／[I006](../issues/I006-otc-foreign-ownership-missing.md) 之後上櫃股各表都有資料）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（5／5） | [run](../runs/2026-11-27_Q017.md) | 補爬 7769 14 列；7769 歷史不足、6488／7769 中小型 |
