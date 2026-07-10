-- raw_ohlcv_daily_twse
-- 上游：ohlcv_daily_twse_list（每個交易日一列 trade_date）
-- 對應 endpoint: GET https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL
-- 上游資料源：TWSE OpenAPI（證交所開放平台）
--
-- 設計理念（rule 9）：一個 (trade_date) 事件 = 一列 raw payload（整個上市市場當日 OHLCV 陣列）。
-- 設計理念（compute-cost）：STOCK_DAY_ALL 一次回應包含當日全體上市證券（~1370 列），把「per-stock」
--   壓縮成「per-day」— API request 數與 payload 數皆降到 1/N（N ≈ 上市檔數）。
--
-- URL 設計說明：
--   * endpoint 實測忽略 ?date=YYYYMMDD 參數，永遠回應最新交易日。
--   * 仍以 `?date=<YYYYMMDD>` 帶入 trade_date 是為了讓 URL 隨列變化 — pg_ivm 對
--     immutable http_get_content 的展開會以 URL 為 cache key，帶動態 URL 可避免 IVM
--     把不同 trade_date 的呼叫視為同一次而 dedupe 掉。
--   * 語義上：每列 payload 對應「該 trade_date 被填入 seed 當下的最新市場快照」。
SELECT
    trade_date,
    custom.http_get_content(
        (
            'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL?date='
            || REPLACE(custom.date_to_iso(trade_date), '-', '')
        )::TEXT
    ) AS ohlcv
FROM {{ schema }}.ohlcv_daily_twse_list;
