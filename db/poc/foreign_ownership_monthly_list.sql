-- foreign_ownership_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/foreign-ownership（上市 TWSE MI_QFIIS／上櫃 TPEx qfii 外資及陸資持股）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 11, 15 退化特例）：外資持股為交易日頻資料（連續型，非離散事件），若逐日抓取
--   會過於密集；本 endpoint 的 as_of 若當日無資料，backend 會往前回溯最近交易日（最多 31 天，
--   見 app/foreign_ownership_source.py MAX_LOOKBACK_DAYS），故以「每月一次」as_of 取樣即可涵蓋
--   每月代表性持股水位，遇假日/非交易日自動回溯不會落空。
-- 邊界（rule 13）：起點 = GREATEST(「掛牌日之後的第一個 5 日」, 2004-02-05)，輸出再取 GREATEST(·, 2004-02-11)。
--   * TWSE MI_QFIIS 實測最早可得日期為 2004-02-11（MIN_DATE）；2004-02 的取樣日落在下限前，
--     改用 2004-02-11 本身，讓老股票保有 2004-02 這個資料點（其餘月份皆為 5 日）。
--   * 起點取掛牌日「之後」的 5 日（掛牌日晚於 5 日則下個月 5 日），不產生掛牌前的列：
--     舊版從「掛牌月 5 日」起，掛牌日晚於 5 日時第一列落在掛牌前，backend 當年無上限回溯，
--     一列就可能打上千次 MI_QFIIS（2026-11-23 實測 20 分鐘累積 1,694 個日檔）。
--   * 上櫃股走 TPEx qfii（I006），實測最早有資料日 2007-05-02，故上櫃股起點下限改為 2007-05-05
--     （app/foreign_ownership_source.py TPEX_MIN_DATE）；更早的列只會回 found=false。
-- 爬取效率：取樣日固定在每月 5 日，與 market_valuation_date_list 同慣例，避開元旦等長假。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（GREATEST 會忽略 NULL）。
SELECT
    stk_code,
    GREATEST(monthly_dates.d::DATE, DATE '2004-02-11') AS month_start_date
FROM {{ schema }}.company_basic_info_list
CROSS JOIN LATERAL generate_series(
        GREATEST(
            CASE WHEN market = '上櫃' THEN DATE '2007-05-05' ELSE DATE '2004-02-05' END,
            make_date(EXTRACT(YEAR FROM listing_date)::INT, EXTRACT(MONTH FROM listing_date)::INT, 5)
                + CASE WHEN EXTRACT(DAY FROM listing_date) > 5 THEN INTERVAL '1 month' ELSE INTERVAL '0 month' END
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    ) AS monthly_dates(d)
WHERE listing_date IS NOT NULL
