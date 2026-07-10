-- ohlcv_daily_twse
-- 上游：raw_ohlcv_daily_twse（每 (trade_date) 一列 payload，內含當日全市場 JSON 陣列）
-- 用途：把 TWSE STOCK_DAY_ALL payload 攤平成 (trade_date, stk_code) 逐股逐日正規化行情。
--
-- 設計理念（rule 12）：本 view 直接使用 seed 的 trade_date（西元 DATE），不從 payload 讀取
--   民國年 Date 欄位——該欄位只用作 raw 層稽核比對，將來需要時可以
--   `make_date(SUBSTRING((item->>'Date'), 1, 3)::INT + 1911, ...)` 展開（同
--   product_revenue_filer_list pattern）。
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw payload 每列（包含 Change="0.0000" 等平盤日），
--   下游要過濾自行處理。
-- 設計理念（rule 16）：只用 CROSS JOIN LATERAL jsonb_array_elements(...) 攤平陣列，
--   pg_ivm 相容；上游 ohlcv 已保證非 NULL 才會被 seed 引用，無需 COALESCE。
--
-- 欄位對齊（與 ohlcv_daily_tpex 一致，方便下游 UNION）：
--   market='sii'（上市）; volume/trade_value/transaction_count 命名對齊 TPEx 版。
SELECT
    trade_date,
    TRIM('"' FROM (item->'Code')::TEXT) AS stk_code,
    TRIM('"' FROM (item->'Name')::TEXT) AS company_name,
    'sii'::TEXT AS market,
    (item->>'OpeningPrice')::NUMERIC AS open,
    (item->>'HighestPrice')::NUMERIC AS high,
    (item->>'LowestPrice')::NUMERIC AS low,
    (item->>'ClosingPrice')::NUMERIC AS close,
    (item->>'TradeVolume')::NUMERIC AS volume,
    (item->>'TradeValue')::NUMERIC AS trade_value,
    (item->>'Transaction')::NUMERIC AS transaction_count,
    (item->>'Change')::NUMERIC AS change
FROM {{ schema }}.raw_ohlcv_daily_twse
CROSS JOIN LATERAL jsonb_array_elements(ohlcv) AS item;
