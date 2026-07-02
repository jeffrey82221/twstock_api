-- dividend_event_list_yfinance
-- 上游：raw_dividend_history_yfinance
-- 與 dividend_event_list 同結構、同意圖；差別在資料源 (yfinance vs FinMind)。
-- 用途：作為 raw_dividend_yfinance 的上游母體。
SELECT 
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (event->>'cash_ex_dividend_date'))) AS cash_ex_dividend_date
FROM {{ schema }}.raw_dividend_history_yfinance
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(dividend_history->'events', '[]'::jsonb)
) AS event
--WHERE (event->>'cash_ex_dividend_date') IS NOT NULL
