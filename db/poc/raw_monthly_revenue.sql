-- raw_monthly_revenue
-- 上游：financial_month_list（每家公司每月一筆 month_start_date，FinMind 專屬 seed）
-- 對應 endpoint: GET /api/company/{stock_id}/revenue?as_of={month_start_date}
-- 上游資料源：FinMind v4 TaiwanStockMonthRevenue
-- 設計理念（rule 14）：月頻資料須有專屬 _list（financial_month_list），
--                    避免與年頻 dividend / financials 混用同一個 financial_year_list。
-- 設計理念（rule 20）：TWSE/MOPS 版 raw_monthly_revenue_twse 已拆到獨立 seed
--                    financial_month_twse_list，本 raw 以 FinMind quota (300 req/hr) 當上限，
--                    不被 TWSE/MOPS 的大跨步 batch 推進相互干擾。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/revenue?as_of=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS revenue,
    month_start_date AS as_of
FROM {{ schema }}.financial_month_list
