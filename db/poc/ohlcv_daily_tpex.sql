-- ohlcv_daily_tpex
-- 上游：raw_ohlcv_daily_tpex（每 (trade_date) 一列 payload，內含當日全市場 JSON 陣列）
-- 用途：把 TPEx tpex_mainboard_daily_close_quotes payload 攤平成 (trade_date, stk_code) 逐股逐日正規化行情。
--
-- 設計理念（rule 12）：本 view 直接使用 seed 的 trade_date（西元 DATE），不從 payload 讀取
--   民國年 Date 欄位——該欄位只用作 raw 層稽核比對，將來需要時可以
--   `make_date(SUBSTRING((item->>'Date'), 1, 3)::INT + 1911, ...)` 展開（同
--   product_revenue_filer_list pattern）。
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw payload 每列（包含未成交等邊角個案），下游要過濾自行處理。
-- 設計理念（rule 16）：只用 CROSS JOIN LATERAL jsonb_array_elements(...) 攤平陣列，
--   pg_ivm 相容；上游 ohlcv 已保證非 NULL 才會被 seed 引用，無需 COALESCE。
--
-- 欄位對齊（與 ohlcv_daily_twse 一致，方便下游 UNION）：
--   market='otc'（上櫃）; TPEx 欄位命名不同（SecuritiesCompanyCode/CompanyName/TradingShares/
--   TransactionAmount/TransactionNumber），此處統一到 stk_code/company_name/volume/trade_value/
--   transaction_count 對齊 TWSE 版。
-- 注意：TPEx `Change` 欄位為含正負號字串（如 "+0.51" / "-0.05"），可直接 CAST NUMERIC。
SELECT
    trade_date,
    TRIM('"' FROM (item->'SecuritiesCompanyCode')::TEXT) AS stk_code,
    TRIM('"' FROM (item->'CompanyName')::TEXT) AS company_name,
    'otc'::TEXT AS market,
    (item->>'Open')::NUMERIC AS open,
    (item->>'High')::NUMERIC AS high,
    (item->>'Low')::NUMERIC AS low,
    (item->>'Close')::NUMERIC AS close,
    (item->>'TradingShares')::NUMERIC AS volume,
    (item->>'TransactionAmount')::NUMERIC AS trade_value,
    (item->>'TransactionNumber')::NUMERIC AS transaction_count,
    (item->>'Change')::NUMERIC AS change
FROM {{ schema }}.raw_ohlcv_daily_tpex
CROSS JOIN LATERAL jsonb_array_elements(ohlcv) AS item;
