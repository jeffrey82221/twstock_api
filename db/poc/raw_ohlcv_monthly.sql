-- raw_ohlcv_monthly
-- 上游：ohlcv_monthly_list（每 (stk_code, month_start_date) 一列；上市 + 上櫃合併）
-- 對應 endpoint：GET http://host.docker.internal:5002/api/ohlcv?stk_code=XXXX&from=YYYY-MM-DD&to=YYYY-MM-DD
--                 （twstock_api 統一 `/api/ohlcv` endpoint；backend 依股票 market 自動選上游）
-- 上游資料源鏈路：raw → twstock_api /api/ohlcv → 上市：TWSE STOCK_DAY / MI_INDEX；上櫃：TPEx tradingStock
--
-- 設計理念（rule 9 · one payload per event）：一個 (stk_code, month_start_date) 事件對應一列 payload。
--   打的 /api/ohlcv 範圍為「該月 1 號 → 該月月底（custom.month_end）」，backend 判定 range_days > 7
--   走 per_stock_month 策略，單次呼叫拿整月日 K（~20 個交易日）。
--
-- 為何走 /api/ohlcv 而非直接打 TWSE / TPEx：統一 endpoint、單位對齊、民國年轉西元、change 合成 signed、
--   共享 backend cache。
--
-- URL 設計：/api/ohlcv 用 `from`/`to`（`YYYY-MM-DD` 西元）。TWSE / TPEx 分流完全在 backend 內部依
--   market 決定（見 ohlcv_monthly_list 註解），seed 與 raw 層都不分流。
--
-- 設計理念（rule 1 / 3）：URL 組合僅使用 IMMUTABLE building blocks（月底用 custom.month_end，
--   不用 ::DATE cast）；http 走 custom.http_get_content。
--
-- payload 結構（供下游正規化參考；以上櫃股為例）：backend 已把 TPEx 單位對齊到「股 / 元」，欄位命名與 TWSE 完全一致：
--   { found: true, stk_code: '5483', market: '上櫃',
--     from_date: '2024-01-01', to_date: '2024-01-31',
--     strategy: 'per_stock_month',
--     rows: [{ trade_date: '2024-01-02', stk_code: '5483',
--              open: 196.0, high: 201.0, low: 193.5, close: 201.0,
--              volume: 6832000.0, trade_value: 1344589000.0,  -- backend 已 *1000
--              transaction_count: 3436.0, change: 5.0 }, ...],
--     source: 'TPEx tradingStock (per-stock-per-month)' }
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
            || custom.date_to_iso(custom.month_end(month_start_date))
        )::TEXT
    ) AS ohlcv,
    listing_date
FROM {{ schema }}.ohlcv_monthly_list;
