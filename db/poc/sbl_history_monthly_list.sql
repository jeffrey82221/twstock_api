-- sbl_history_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/sbl-history（TWSE SBL t13sa870 借券歷史還券明細）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 11, 15 退化特例）：借券還券為事件性資料，但本服務並未提供「歷史全量」
--   endpoint（不同於 dividend/history）。backend 以 as_of 往前「一個 31 日視窗」尋找還券事件
--   （app/twse_sbl_source.py MAX_LOOKBACK_DAYS = WINDOW_DAYS = 31），故以「每月一次」as_of 取樣
--   可讓每次呼叫的 31 日視窗彼此首尾相接，覆蓋全部月份且每列只打一次上游。
--   （backend 舊版會以 31 日視窗一路回溯到 SBL_MIN_DATE，沒有還券事件的股票一列可打上百次。）
-- 邊界（rule 13）：起點 = GREATEST(「掛牌日之後的第一個 5 日」, 2005-02-05)。
--   * TWSE SBL t13sa870 實測 2330 最早可得完成還券資料為 2005-01-28（SBL_MIN_DATE）；其後第一個
--     5 日為 2005-02-05。舊版會產生 2005-01-05（早於 SBL_MIN_DATE，endpoint 直接回 400）。
--   * 起點取掛牌日「之後」的 5 日，不產生掛牌前的列。
-- 爬取效率：取樣日固定在每月 5 日，與其他新增的月度取樣 seed 同慣例（一致的排程可讀性）。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（GREATEST 會忽略 NULL）。
SELECT
    stk_code,
    generate_series(
        GREATEST(
            DATE '2005-02-05',
            make_date(EXTRACT(YEAR FROM listing_date)::INT, EXTRACT(MONTH FROM listing_date)::INT, 5)
                + CASE WHEN EXTRACT(DAY FROM listing_date) > 5 THEN INTERVAL '1 month' ELSE INTERVAL '0 month' END
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
