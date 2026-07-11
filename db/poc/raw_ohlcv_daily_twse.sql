-- raw_ohlcv_daily_twse
-- 上游：ohlcv_daily_twse_list（每 (stk_code, month_start_date) 一列）
-- 對應 endpoint：GET http://host.docker.internal:5002/api/ohlcv?stk_code=XXXX&from=YYYY-MM-DD&to=YYYY-MM-DD
--                 （twstock_api 統一 `/api/ohlcv` endpoint；backend 依股票 market 自動選 TWSE STOCK_DAY 上游）
-- 上游資料源鏈路：raw → twstock_api /api/ohlcv → TWSE 證交所 exchangeReport/STOCK_DAY（per-stock-per-month）
--
-- 設計理念（rule 9 · one payload per event）：一個 (stk_code, month_start_date) 事件對應一列 payload。
--   打的 /api/ohlcv 範圍為「該月 1 號 → 該月月底」，backend 判定 range_days > 7 走 per_stock_month 策略、
--   單次呼叫 TWSE STOCK_DAY 拿整月日 K（~20 個交易日）。相對「per-stock-per-day」節省 20× 上游流量。
--
-- 為何走 /api/ohlcv 而非直接打 TWSE：
--   1. Backend 已把 TPEx 上游「張 / 仟元」單位對齊「股 / 元」，raw 層不再需要 * 1000 換算魔數
--   2. Backend 已把民國年 'YYY/MM/DD' 轉西元 ISO 'YYYY-MM-DD'，normalized 層不再需要 make_date + SUBSTRING
--   3. Backend 已把「漲跌方向 + 漲跌價差」合成 signed change，normalized 層不再需要 TRIM/LEADING '+'
--   4. TWSE / TPEx 兩條 pipeline 共享同一 backend endpoint，未來若上游 URL 變動只需改 backend
--   5. Backend 有磁碟 cache（per-day 全市場 payload）與智能切換，可跨 pipeline 重用下載
--
-- URL 設計：/api/ohlcv 用 `from`/`to`（`YYYY-MM-DD` 西元）；本 view 用 month_start_date 為 from、
--   當月月底日期為 to（+ interval '1 month' - 1 day）。
--
-- 設計理念（rule 3）：URL 組合僅使用 IMMUTABLE building blocks（custom.date_to_iso、字串串接、算術），
--   http 呼叫走既有 IMMUTABLE wrapper custom.http_get_content。
--
-- payload 結構（供下游正規化參考）：
--   { found: true, stk_code: '2330', market: '上市',
--     from_date: '2024-01-01', to_date: '2024-01-31',
--     strategy: 'per_stock_month',
--     rows: [{ trade_date: '2024-01-02', stk_code: '2330',
--              open: 590.0, high: 593.0, low: 589.0, close: 593.0,
--              volume: 27997826.0, trade_value: 16549619798.0,
--              transaction_count: 20667.0, change: 0.0 }, ...],
--     source: 'TWSE STOCK_DAY (per-stock-per-month)' }
--   found=false（stk_code 不在 basic table）或該月無交易日：rows=[]，正規化層 LATERAL 自然攤平 0 列。
SELECT
    stk_code,
    month_start_date,
    custom.http_get_content(
        (
            'http://host.docker.internal:5002/api/ohlcv?stk_code='
            || stk_code
            || '&from='
            || custom.date_to_iso(month_start_date)
            || '&to='
            || custom.date_to_iso(
                (month_start_date + INTERVAL '1 month' - INTERVAL '1 day')::DATE
            )
        )::TEXT
    ) AS ohlcv
FROM {{ schema }}.ohlcv_daily_twse_list;
