-- company_valuation_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company-valuation（單一上市公司估值展開；反推 EPS/BVPS/DPS）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 11）：本 endpoint 與 /api/market-valuation-summary 共用同一份 TWSE BWIBBU_d
--   payload（backend 磁碟 cache 共用），為交易日頻連續資料、無回溯 fallback，
--   以「每月一次」as_of 取樣作為 PoC 代表性採樣。
-- 邊界：TWSE BWIBBU_d 資料起始日為 2005-09-02（app/valuation_source.py VALUATION_MIN_DATE）。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（generate_series 起點不能是 NULL）。
SELECT
    stk_code,
    generate_series(
        make_date(
            EXTRACT(YEAR FROM GREATEST(listing_date, DATE '2005-09-02'))::INT,
            EXTRACT(MONTH FROM GREATEST(listing_date, DATE '2005-09-02'))::INT,
            1
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
