-- mops_quarterly_financials_quarter_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/quarterly-financials（MOPS t163sb06 + t163sb04）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 14）：季度屬獨立時間維度，須有專屬 _list，不與月頻 financial_month_list
--   或年頻 financial_year_list 混用。
-- 設計理念（rule 20）：本 seed 專供 MOPS 版季度財務分析，與既有 FinMind/yfinance 版季度
--   （financial_quarter_yfinance_list）資料源不同、限流特性不同，分流各自 seed。
-- 每公司從設立日所在「季」的季末月起，以 3 個月為步長直接產生季末日（3/6/9/12 月月末），
-- 對齊 MOPS `season` 概念（1..4 季）。
-- Point-in-time（I002，app/pit.py）：下游 as_of 打 `as_of_date` = 該季財報的法定公告期限
--   （Q1 5/15、Q2 8/31（金融業期限；一般公司 8/14 已公告）、Q3 11/14、Q4 隔年 3/31），
--   endpoint 只回 as_of 當天已到期限的季度，所以每列恰好對應 quarter_end 那一季。
--   未到期限的季度不產生（as_of_date <= CURRENT_DATE，屬 rule 13 的資料邊界對齊）。
-- 設計理念（rule 13）：季末邊界直接寫進 generate_series（起點對齊到季末月、步長 3 個月），
--   不再「逐月展開再用 WHERE 留季末月」：輸出相同，但每次 cron tick 計算 hidden 時少算
--   2/3 的列（實測 276 家公司 16.2ms → 3.8ms）。
-- 邊界：MOPS t163sb06 實測最早可得季度為 2013-03-31（app/mops_quarterly_financials.py
--   QUARTERLY_MIN_PERIOD），故起點鎖定 GREATEST(incorporation_date, 2013-01-01)；其公告期限
--   2013-05-15 即 endpoint 的 as_of 下限（QUARTERLY_MIN_DATE）。
SELECT stk_code, quarter_end, as_of_date
FROM (
    SELECT
        stk_code,
        quarter_end,
        CASE EXTRACT(MONTH FROM quarter_end)::INT
            WHEN 3 THEN quarter_end + 45                                         -- 05-15
            WHEN 6 THEN quarter_end + 62                                         -- 08-31
            WHEN 9 THEN quarter_end + 45                                         -- 11-14
            ELSE make_date(EXTRACT(YEAR FROM quarter_end)::INT + 1, 3, 31)       -- 隔年 03-31
        END AS as_of_date
    FROM (
        SELECT
            stk_code,
            (
                generate_series(
                    make_date(
                        EXTRACT(YEAR FROM GREATEST(incorporation_date, DATE '2013-01-01'))::INT,
                        -- 該月所在季的季末月：1-3→3、4-6→6、7-9→9、10-12→12
                        ((EXTRACT(MONTH FROM GREATEST(incorporation_date, DATE '2013-01-01'))::INT + 2) / 3) * 3,
                        1
                    ),
                    CURRENT_DATE,
                    INTERVAL '3 months'
                ) + INTERVAL '1 month' - INTERVAL '1 day'
            )::DATE AS quarter_end
        FROM {{ schema }}.company_basic_info_list
    ) AS quarters
) AS deadlines
WHERE as_of_date <= CURRENT_DATE
