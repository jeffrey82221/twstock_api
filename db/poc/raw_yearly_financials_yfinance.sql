-- raw_yearly_financials_yfinance
-- 上游：financial_year_yfinance_list（每家公司每個年初 as_of）
-- 對應 endpoint: GET /api/company/{stock_id}/financials/yfinance?as_of={year_start_date}
-- 上游資料源：yfinance Python Library (Yahoo Finance)
-- 與 raw_yearly_financials 結構一致，純資料源替代版，方便對比。
-- 注意：上游 seed 已從 financial_year_list 換為 financial_year_yfinance_list,
-- 兩張 raw view 各自獨立填充 pop schema (見 rule 20 分流爬取)。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/financials/yfinance?as_of=' || custom.date_to_iso(year_start_date)::TEXT)::TEXT
    ) AS financials,
    year_start_date AS as_of
FROM {{ schema }}.financial_year_yfinance_list
