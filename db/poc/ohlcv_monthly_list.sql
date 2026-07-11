-- ohlcv_monthly_list
-- 上游：company_basic_info_list
-- 用途：每檔個股 × 每月一列的「日 K 事件母體」（上市 + 上櫃合併，backend 依 market 內部分發）。
--       搭配 twstock_api `/api/ohlcv` endpoint (per-stock-per-month payload) 使用；下游
--       raw_ohlcv_monthly 對每 (stk_code, month_start_date) 打一次 HTTP 到 /api/ohlcv?from=月初&to=月底。
--
-- 設計理念（rule 20 · 資料源分流）：seed 層不做分流；分流已下沉到 backend `/api/ohlcv` 內部：
--   * 上市 + 2010-01-04 以後：走 TWSE STOCK_DAY (per-stock-per-month)
--   * 上市 + 2010-01-04 以前：走 TWSE MI_INDEX (per-day-market)（早期 STOCK_DAY 官方拒收）
--   * 上櫃：一律走 TPEx tradingStock (per-stock-per-month)
--
-- 設計理念（rule 15 · 母體大小）：seed 從 max(listing_date 所屬月, 民國 93/2/11 = 2004-02-11) 起
--   generate_series 到 CURRENT_DATE，月粒度。~1900 檔（上市 + 上櫃）× 平均 ~15 年 × 12 月
--   ≈ 340k 列 seed 上限。
--
-- 資料可得性下限（民國 93/2/11 = 2004-02-11）：三家上游最寬鬆下限。
--   * TWSE STOCK_DAY: hard block 「查詢日期小於 99 年 1 月 4 日 (2010-01-04)」
--   * TWSE MI_INDEX:  hard block 「查詢日期小於 93 年 2 月 11 日 (2004-02-11)」← seed 下限依此設
--   * TPEx tradingStock / dailyQuotes: 無官方下限（endpoint 接受任何日期，該檔上櫃前回空表）
--   seed 以 GREATEST(..., DATE '2004-02-01') 剃除，避免上市個股在 2004-02 前打上游必失敗。
--   注意 2004-02-01 是月粒度 anchor；2004-02 那個月 backend 打 MI_INDEX 2/11-2/29，2/1-2/10 上游無資料自動落空。
--   若要延伸到 1994-10-01（FinMind 起點），需另接第三方 driver（未實作）。
--
-- 設計理念（rule 3 / rule 16）：僅 IMMUTABLE building blocks；pg_ivm 相容。
-- 設計理念（rule 6）：(stk_code, month_start_date) 唯一。
-- 設計理念（rule 13）：不做業務過濾；listing_date IS NOT NULL 為技術性 guard；
--   2004-02-01 下限為「資料源可得性」guard，非業務過濾。
SELECT
    stk_code,
    generate_series(
        GREATEST(
            make_date(EXTRACT(YEAR FROM listing_date)::INT, EXTRACT(MONTH FROM listing_date)::INT, 1),
            DATE '2010-02-01'
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS month_start_date,
    listing_date
FROM {{ schema }}.company_basic_info_list;
