-- mops_quarterly_financials_quarter_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/quarterly-financials（MOPS t163sb06 + t163sb04）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 14）：季度屬獨立時間維度，須有專屬 _list，不與月頻 financial_month_list
--   或年頻 financial_year_list 混用。
-- 設計理念（rule 20）：本 seed 專供 MOPS 版季度財務分析，與既有 FinMind/yfinance 版季度
--   （financial_quarter_yfinance_list）資料源不同、限流特性不同，分流各自 seed。
-- 每公司從設立日所在月份起、逐月展開後只保留真正的「季末月」（3/6/9/12 月）月末日，
-- 對齊 MOPS `season` 概念（1..4 季）；下游 as_of 直接打該季末日。
-- 邊界：MOPS t163sb06 實測最早可得季度為 2013-03-31（app/mops_quarterly_financials.py
--   QUARTERLY_MIN_DATE），故起點鎖定 GREATEST(incorporation_date, 2013-01-01)。
SELECT stk_code, month_end AS quarter_end
FROM (
    SELECT
        stk_code,
        (
            generate_series(
                make_date(
                    EXTRACT(YEAR FROM GREATEST(incorporation_date, DATE '2013-01-01'))::INT,
                    EXTRACT(MONTH FROM GREATEST(incorporation_date, DATE '2013-01-01'))::INT,
                    1
                ),
                CURRENT_DATE,
                INTERVAL '1 month'
            ) + INTERVAL '1 month' - INTERVAL '1 day'
        )::DATE AS month_end
    FROM {{ schema }}.company_basic_info_list
) months
WHERE EXTRACT(MONTH FROM month_end) IN (3, 6, 9, 12)
