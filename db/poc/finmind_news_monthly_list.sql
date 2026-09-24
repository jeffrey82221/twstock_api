-- finmind_news_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company/{stock_id}/news/finmind（FinMind TaiwanStockNews 個股新聞）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 15 附註）：新聞為事件／文字資料，此 endpoint 明確禁止 interpolation
--   （當日無新聞直接 404，不回溯其他日期），本服務也未提供「新聞歷史」事件母體 endpoint。
--   PoC 階段以「每月一次」as_of 取樣作為代表性抽樣（會有大量月份剛好無新聞、found=false，
--   屬已知限制，未來如需提高命中率可考慮新增 `/news/finmind/history` 事件母體 endpoint，
--   走 rule 15 的完整做法）。
-- 邊界：FinMind TaiwanStockNews 卡片研究下限為 2019-01-01（app/finmind_news_source.py MIN_DATE）。
-- 爬取效率：本 endpoint 無回溯 fallback（當日無新聞直接 404），取樣日固定在每月 5 日
--   （而非 1 日）可略為降低命中元旦等長假的機率。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（generate_series 起點不能是 NULL）。
SELECT
    stk_code,
    generate_series(
        make_date(
            EXTRACT(YEAR FROM GREATEST(listing_date, DATE '2019-01-01'))::INT,
            EXTRACT(MONTH FROM GREATEST(listing_date, DATE '2019-01-01'))::INT,
            5
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
