---
id: Q019
title: 2330、2317、2454、1101、6488 最近一年流動比率 > 150% 且負債比 < 50% 嗎？年度財務比率彼此對得上嗎？
strategy: quality            # §5.1 體質
status: passing
stocks: ["2330", "2317", "2454", "1101", "6488", "2882"]
window: {since: 2023, until: 2025}
pop_tables: [financial_analysis]
crawl:                       # 出題時（2026-11-27）只有 6488 沒有年度財務比率
  - seed: financial_analysis_year_list
    filters: {stk_code: ["6488"]}
    order_by: ["year"]
    since: 2022
    budget: 4                # MOPS t51sb02；2022–2025
issues: []
last_run: 2026-11-27
---

## 決策情境
最近一個年度（2025）流動比率 > 150% 且負債比 < 50% → 「財務穩健」，可以承受景氣下行；兩者任一不符 → 不列入。
2882（金控）只當邊界案例驗資料，不做決策：金融業的流動比率、負債比與一般公司不可比。

## 決策 SQL
```sql
SELECT stk_code, year, current_ratio, quick_ratio, debt_ratio,
       current_ratio > 150 AND debt_ratio < 50 AS sound
FROM pop.financial_analysis
WHERE stk_code IN ('2330','2317','2454','1101','6488') AND year = 2025
ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 六檔 2023–2025 每年都有負債比與流動比率
```sql
SELECT s.stk_code, y AS year
FROM unnest(ARRAY['2330','2317','2454','1101','6488','2882']) AS s(stk_code)
CROSS JOIN generate_series(2023, 2025) AS y
WHERE NOT EXISTS (SELECT 1 FROM pop.financial_analysis f
                  WHERE f.stk_code = s.stk_code AND f.year = y AND f.debt_ratio IS NOT NULL AND f.current_ratio IS NOT NULL)
```

### C2 reasonableness — 速動比率不大於流動比率（速動資產是流動資產的子集，容許 0.01 的捨入）
全表 296 列 0 違規（2026-11-27）。抓兩欄對調或錯位。
```sql
SELECT stk_code, year, current_ratio, quick_ratio FROM pop.financial_analysis
WHERE stk_code IN ('2330','2317','2454','1101','6488','2882') AND year BETWEEN 2023 AND 2025
  AND quick_ratio > current_ratio + 0.01
```

### C3 internal — 平均收現日數 ≈ 365 ÷ 應收款項週轉率、平均銷貨日數 ≈ 365 ÷ 存貨週轉率（誤差 2% 或 1 天）
MOPS t51sb02 分別公布這四欄（`app/mops_financial_analysis.py` 只解析，不推導），所以是獨立欄位的互證，抓欄位錯位。全表各 273 列可比對、0 違規（2026-11-27）。
```sql
SELECT stk_code, year, ar_turnover, avg_collection_days, round(365 / ar_turnover, 2) AS calc_ar_days,
       inventory_turnover, avg_sales_days, round(365 / NULLIF(inventory_turnover, 0), 2) AS calc_inv_days
FROM pop.financial_analysis
WHERE stk_code IN ('2330','2317','2454','1101','6488','2882') AND year BETWEEN 2023 AND 2025
  AND ((ar_turnover > 0 AND abs(avg_collection_days - 365 / ar_turnover) > greatest(1, 0.02 * avg_collection_days))
    OR (inventory_turnover > 0 AND abs(avg_sales_days - 365 / inventory_turnover) > greatest(1, 0.02 * avg_sales_days)))
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 15：五檔非金融 × 3 年，2882 沒有週轉率）：
```sql
SELECT count(*) FROM pop.financial_analysis
WHERE stk_code IN ('2330','2317','2454','1101','6488','2882') AND year BETWEEN 2023 AND 2025 AND ar_turnover > 0
```

### C4 validity — 負債比 0–100%、流動比率 ≥ 0、ROE 介於 −100% 與 100%
```sql
SELECT stk_code, year, debt_ratio, current_ratio, roe FROM pop.financial_analysis
WHERE stk_code IN ('2330','2317','2454','1101','6488','2882') AND year BETWEEN 2023 AND 2025
  AND (debt_ratio NOT BETWEEN 0 AND 100 OR current_ratio < 0 OR roe NOT BETWEEN -100 AND 100)
```

### C5 freshness — 最新年度 = 已過年報期限（3/31）的最近一年
台北時間今天往前推 3 個月所在年度的前一年（2026-11-27 → 2025）。
```sql
SELECT s.stk_code, max(f.year) AS latest_year,
       extract(year FROM (now() AT TIME ZONE 'Asia/Taipei')::date - INTERVAL '3 months')::int - 1 AS expected_year
FROM unnest(ARRAY['2330','2317','2454','1101','6488','2882']) AS s(stk_code)
LEFT JOIN pop.financial_analysis f ON f.stk_code = s.stk_code AND f.debt_ratio IS NOT NULL
GROUP BY s.stk_code
HAVING max(f.year) IS DISTINCT FROM extract(year FROM (now() AT TIME ZONE 'Asia/Taipei')::date - INTERVAL '3 months')::int - 1
```

## 已知限制
- 2882 的流動比率是 1,655%（2025），速動比率、週轉率是 NULL：金融業的資產負債表沒有流動／非流動之分，MOPS 仍給出一個比率。只用來驗資料，不做決策（[I009](../issues/I009-mops-quarterly-financial-companies.md) 同類）。
- C5 的「3 月底」用年度近似：4 月 1 日到 3 個月後（7 月 1 日）之間，期限已過但式子仍期待前一年，會少報不會誤報。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（5／5） | [run](../runs/2026-11-27_Q019.md) | 補爬 6488 4 列；只有 2330 財務穩健；年報驗算一致 |
