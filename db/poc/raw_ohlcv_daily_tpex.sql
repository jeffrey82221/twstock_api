-- raw_ohlcv_daily_tpex
-- 上游：ohlcv_daily_tpex_list（每個交易日一列 trade_date）
-- 對應 endpoint: GET https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
-- 上游資料源：TPEx OpenAPI（櫃買中心開放平台）
--
-- 設計理念（rule 9）：一個 (trade_date) 事件 = 一列 raw payload（整個上櫃市場當日 OHLCV 陣列）。
-- 設計理念（compute-cost）：一次回應包含當日全體上櫃主板證券（~10000 列，含 ETF/受益證券等），
--   把「per-stock」壓縮成「per-day」— API request 數與 payload 數皆降到 1/N（N ≈ 上櫃檔數）。
--
-- URL 設計說明：
--   * endpoint 無 date 參數可指定日期，永遠回應最新交易日。
--   * 仍將 trade_date 以 `?_trade_date=YYYYMMDD` fragment-like query 帶入 URL，使 URL 隨列而異。
--     TPEx OpenAPI 不識別未知 query 參數，不影響回應內容；目的是避免 pg_ivm 對 immutable
--     http_get_content 以 URL 為 cache key 時，不同 trade_date 但同 URL 被誤 dedupe。
--     語義上：每列 payload 對應「該 trade_date 被寫入 seed 當下的最新市場快照」。
SELECT
    trade_date,
    custom.http_get_content(
        (
            'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes?_trade_date='
            || REPLACE(custom.date_to_iso(trade_date), '-', '')
        )::TEXT
    ) AS ohlcv
FROM {{ schema }}.ohlcv_daily_tpex_list;
