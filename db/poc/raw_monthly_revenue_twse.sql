-- raw_monthly_revenue_twse
-- 上游：financial_year_list（每家公司每個年初 as_of）
-- 對應 endpoint: GET /api/company/{stock_id}/revenue/twse?as_of={year_start_date}
-- 上游資料源：證交所體系（TWSE OpenAPI t187ap05 最新月 + MOPS t21sc03 歷史月營收）
-- 與 raw_monthly_revenue (FinMind 版) 結構一致，純資料源替代版，方便對比
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/revenue/twse?as_of=' || custom.date_to_iso(year_start_date)::TEXT)::TEXT
    ) AS revenue,
    year_start_date AS as_of
FROM {{ schema }}.financial_year_list
