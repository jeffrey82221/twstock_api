-- sbl_history_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/sbl-history（TWSE SBL t13sa870 借券歷史還券明細）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 11, 15 退化特例）：借券還券為事件性資料，但本服務並未提供「歷史全量」
--   endpoint（不同於 dividend/history），backend 以 31 日視窗回溯尋找最近一筆事件
--   （見 app/twse_sbl_source.py），故以「每月一次」as_of 取樣可讓每次呼叫的 31 日回溯視窗
--   彼此首尾相接，覆蓋全部月份而不重複打過於密集的日期。
-- 邊界：TWSE SBL t13sa870 實測 2330 最早可得完成還券資料為 2005-01-28
--   （app/twse_sbl_source.py SBL_MIN_DATE）。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（generate_series 起點不能是 NULL）。
SELECT
    stk_code,
    generate_series(
        make_date(
            EXTRACT(YEAR FROM GREATEST(listing_date, DATE '2005-01-28'))::INT,
            EXTRACT(MONTH FROM GREATEST(listing_date, DATE '2005-01-28'))::INT,
            1
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
