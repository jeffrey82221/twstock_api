-- financial_month_list
-- 每公司從成立日起、每月一筆 month_start_date（該月 1 日）
-- 用途：作為月頻資料（月營收 /revenue、/revenue/twse）的上游母體
-- 設計理念（rule 11）：
--   月營收公布頻率為每月一次（次月 10 日前），因此用「每月一次」的 as_of 已足夠遍歷所有可能的月份切片，
--   不需要以日為單位密集抓取。此表把 endpoint 可能的 as_of input space 壓到最小、無重複，
--   讓下游 raw_monthly_revenue 對每個 (stk_code, month) 只打一次 API。
SELECT
    stk_code,
    generate_series(
        make_date(EXTRACT(YEAR FROM incorporation_date)::INT, EXTRACT(MONTH FROM incorporation_date)::INT, 1),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info;
