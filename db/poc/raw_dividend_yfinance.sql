-- raw_dividend_yfinance
-- 上游：dividend_event_list_yfinance（每公司每個真實除息日一筆）
-- 對應 endpoint: GET /api/company/{stock_id}/dividend/yfinance?as_of={cash_ex_dividend_date}
-- 上游資料源：yfinance Python Library — Ticker.dividends
--
-- 與 raw_dividend (FinMind 版) 結構完全一致，純資料源替代版。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/dividend/yfinance?as_of=' || custom.date_to_iso(cash_ex_dividend_date)::TEXT)::TEXT
    ) AS dividend,
    cash_ex_dividend_date AS as_of
FROM {{ schema }}.dividend_event_list_yfinance
