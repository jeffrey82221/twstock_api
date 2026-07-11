-- ohlcv_daily_tpex_list
-- 上游：company_basic_info（過濾 market='上櫃'）
-- 用途：TPEx 上櫃每檔個股 × 每月一列的「日 K 事件母體」。搭配 twstock_api `/api/ohlcv` endpoint
--       (per-stock-per-month payload) 使用；下游 raw_ohlcv_daily_tpex 對每 (stk_code, month_start_date)
--       打一次 HTTP 到 /api/ohlcv?from=月初&to=月底，backend 依 market='上櫃' 自動分發到 TPEx tradingStock。
--
-- 設計理念（rule 20 · 資料源分流）：與 ohlcv_daily_twse_list 分流；雖然 backend endpoint 統一，
--   上市 / 上櫃股數量 / 限流 / 交易日分佈不同，分成兩張獨立 seed 可各自控 batch_size。
--   Backend 內部仍依 company_basic_info.market 分發到不同上游（TWSE / TPEx）、保留層次性。
--
-- 設計理念（rule 15 · 母體大小）：seed 從 listing_date 起 generate_series 到 CURRENT_DATE，
--   月粒度。以 ~800 檔上櫃 × 平均 10 年 × 12 月 ≈ 96k 列 seed 上限。
--
-- 設計理念（rule 3 / rule 16）：僅 IMMUTABLE building blocks；pg_ivm 相容。
-- 設計理念（rule 6）：(stk_code, month_start_date) 唯一。
-- 設計理念（rule 13）：不做業務過濾；listing_date IS NOT NULL 為技術性 guard。
SELECT
    stk_code,
    generate_series(
        make_date(EXTRACT(YEAR FROM listing_date)::INT, EXTRACT(MONTH FROM listing_date)::INT, 1),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date,
    listing_date
FROM {{ schema }}.company_basic_info_list;
