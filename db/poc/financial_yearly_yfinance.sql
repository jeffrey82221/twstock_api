-- financial_yearly_yfinance
-- 上游：raw_yearly_financials_yfinance
-- 攤平：每行 = 該公司在某 year_start_date as_of 下，由 yfinance 取得的 TTM 指標一筆
-- 欄位刻意與 financial_quarterly / financial_quarterly_yfinance 完全 align，差別只在資料源與時間關鍵
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows（含 eps/net_income 為 jsonb null 的舊年代）。
-- 型別安全：所有 numeric 欄位一律走 `->>`（回傳 text）再 ::NUMERIC，jsonb null 會安全轉為 SQL NULL。
SELECT
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (financials->'as_of')::TEXT)) AS as_of,
    TRIM('"' FROM (financials->'stock_id')::TEXT) AS stock_id,
    (financials->'eps'->>'ttm')::NUMERIC AS eps_ttm,
    custom.parse_iso_date(TRIM('"' FROM (financials->'eps'->'latest_quarter_date')::TEXT)) AS latest_quarter_date,
    (financials->'eps'->>'latest_quarter_value')::NUMERIC AS latest_quarter_eps,
    (financials->'net_income'->>'ttm')::NUMERIC AS net_income_ttm,
    (financials->'net_income'->>'latest_quarter_value')::NUMERIC AS latest_quarter_net_income,
    (financials->>'operating_margin_pct')::NUMERIC AS operating_margin_pct,
    (financials->>'revenue_ttm_from_financial_statements')::NUMERIC AS revenue_ttm
FROM {{ schema }}.raw_yearly_financials_yfinance
