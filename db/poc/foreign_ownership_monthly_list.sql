-- foreign_ownership_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/foreign-ownership（TWSE MI_QFIIS 外資及陸資持股）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 11, 15 退化特例）：外資持股為交易日頻資料（連續型，非離散事件），若逐日抓取
--   會過於密集；本 endpoint 的 as_of 若當日無資料，backend 會自動逐日往前回溯找最近交易日
--   （見 app/foreign_ownership_source.py），故以「每月一次」as_of 取樣即可涵蓋每月代表性持股
--   水位，遇假日/非交易日自動回溯不會落空。
-- 邊界：TWSE MI_QFIIS 實測最早可得日期為 2004-02-11（app/foreign_ownership_source.py MIN_DATE）。
-- 爬取效率：取樣日固定在每月 5 日（而非 1 日），與 market_valuation_date_list 同慣例，
--   避開元旦等長假造成的無效呼叫。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（generate_series 起點不能是 NULL）。
SELECT
    stk_code,
    generate_series(
        make_date(
            EXTRACT(YEAR FROM GREATEST(listing_date, DATE '2004-02-11'))::INT,
            EXTRACT(MONTH FROM GREATEST(listing_date, DATE '2004-02-11'))::INT,
            5
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
