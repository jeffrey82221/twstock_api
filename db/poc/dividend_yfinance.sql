-- dividend_yfinance
-- 上游：raw_dividend_yfinance
-- 欄位完全與 dividend (FinMind 版) align，差別在資料源 (yfinance vs FinMind)。
-- yfinance 不提供：stock_dividend / cash_payment_date / stock_ex_dividend_date / announcement_date
-- 對應欄位於上游 endpoint 已回 null/0，本 view 保留相同欄位以維持 schema align。
--
-- 設計理念（rule 15）：每列對應歷史一次真實除息事件（by cash_ex_dividend_date）。
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體所有 rows。
SELECT
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'as_of')::TEXT)) AS as_of,
    TRIM('"' FROM (dividend->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (dividend->'dividend'->>'year')) AS dividend_year,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'reference_date'))) AS reference_date,
    (dividend->'dividend'->>'cash_dividend')::NUMERIC AS cash_dividend,
    (dividend->'dividend'->>'stock_dividend')::NUMERIC AS stock_dividend,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'cash_ex_dividend_date'))) AS cash_ex_dividend_date,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'cash_payment_date'))) AS cash_payment_date,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'stock_ex_dividend_date'))) AS stock_ex_dividend_date,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'announcement_date'))) AS announcement_date
FROM {{ schema }}.raw_dividend_yfinance
