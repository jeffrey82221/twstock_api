-- institutional_monthly_list
-- 新增 endpoint 覆蓋：GET /api/institutional-net-buy-sell（三大法人買賣超日報；單股 x 單日）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 11）：三大法人買賣超為交易日頻連續資料。此 endpoint 沒有回溯 fallback
--   （非交易日回 `row=null`），以「每月一次」as_of 取樣即足以做為 PoC 代表性採樣，
--   避免以日為單位密集抓取造成過大母體。
-- 邊界：資料起始日為民國 101/5/2 = 2012-05-02
--   （app/institutional_source.py INSTITUTIONAL_MIN_DATE）。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（generate_series 起點不能是 NULL）。
SELECT
    stk_code,
    generate_series(
        make_date(
            EXTRACT(YEAR FROM GREATEST(listing_date, DATE '2012-05-02'))::INT,
            EXTRACT(MONTH FROM GREATEST(listing_date, DATE '2012-05-02'))::INT,
            1
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
