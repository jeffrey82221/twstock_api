-- raw_dividend_history_yfinance
-- 上游：company_list（每家公司抓一次）
-- 對應 endpoint: GET /api/company/{stock_id}/dividend/history/yfinance
-- 上游資料源：yfinance Python Library — Ticker.dividends（整段歷史）
--
-- 與 raw_dividend_history (FinMind 版) 結構完全一致，純資料源替代版。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/dividend/history/yfinance')::TEXT
    ) AS dividend_history
FROM {{ schema }}.company_list
