-- raw_finmind_news
-- 上游：finmind_news_monthly_list（每公司每月一筆 month_start_date）
-- 對應 endpoint: GET /api/company/{stock_id}/news/finmind?as_of={month_start_date}
-- 上游資料源：FinMind v4 TaiwanStockNews
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code
            || '/news/finmind?as_of=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS finmind_news,
    month_start_date AS as_of
FROM {{ schema }}.finmind_news_monthly_list
