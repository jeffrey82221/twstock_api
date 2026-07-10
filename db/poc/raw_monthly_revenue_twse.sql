-- raw_monthly_revenue_twse
-- 上游：financial_month_twse_list（每家公司每月一筆 month_start_date，TWSE 專屬 seed）
-- 對應 endpoint: GET /api/company/{stock_id}/revenue/twse?as_of={month_start_date}
-- 上游資料源：證交所體系（TWSE OpenAPI t187ap05 最新月 + MOPS t21sc03 歷史月營收）
-- 與 raw_monthly_revenue (FinMind 版) 結構一致，純資料源替代版，方便對比。
-- 設計理念（rule 20）：與 raw_monthly_revenue 分流爬取，各自對應獨立 pop.<seed> 空表，
--                     可用不同 batch limit 進度填充；TWSE/MOPS 限流寬於 FinMind，本 seed 可
--                     拉高 batch_size 快速拉齊，不受 FinMind 300 req/hr 拖累。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/revenue/twse?as_of=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS revenue,
    month_start_date AS as_of
FROM {{ schema }}.financial_month_twse_list
