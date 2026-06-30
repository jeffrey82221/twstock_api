-- raw_yearly_dividend_yfinance
-- 上游：financial_year_list（每家公司每個年初 as_of）
-- 對應 endpoint: GET /api/company/{stock_id}/dividend/yfinance?as_of={year_start_date}
-- 上游資料源：yfinance Python Library (Yahoo Finance) — ticker.dividends
-- 與 raw_yearly_dividend (FinMind 版) 結構一致，純資料源替代版，方便對比
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/dividend/yfinance?as_of=' || custom.date_to_iso(year_start_date)::TEXT)::TEXT
    ) AS dividend,
    year_start_date AS as_of
FROM {{ schema }}.financial_year_list
