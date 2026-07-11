-- ohlcv_daily_tpex
-- 上游：raw_ohlcv_daily_tpex（每 (stk_code, month_start_date) 一列，內含該月日 K JSON）
-- 用途：把 /api/ohlcv payload 攤平成 (trade_date, stk_code) 逐股逐日正規化行情。
--
-- 設計理念（rule 16 · pg_ivm 相容）：只用 CROSS JOIN LATERAL jsonb_array_elements(ohlcv->'rows')。
-- 設計理念（rule 12）：/api/ohlcv 已由 backend 轉為西元 ISO；normalized 層直接 CAST。
-- 設計理念（rule 13）：不做業務過濾；rows=[] → 0 列。
-- 設計理念（NULL-safe）：COALESCE(ohlcv->'rows', '[]'::jsonb) 防 refresh 中斷。
--
-- 欄位對齊 ohlcv_daily_twse：market='otc'（上櫃）；欄位命名、單位皆對齊 TWSE 版。
--
-- 單位對齊：backend 已把 TPEx 上游「張 / 仟元」統一乘 1000 對齊「股 / 元」，
--   normalized 層不再需要在 SQL 內乘 1000（原本 TPEx-only 的 *1000 魔數已消失）。
--
-- rows[*] 欄位：與 ohlcv_daily_twse 完全相同 schema，backend 已保證。
SELECT
    (item->>'trade_date')::DATE AS trade_date,
    item->>'stk_code' AS stk_code,
    'otc'::TEXT AS market,
    (item->>'open')::NUMERIC AS open,
    (item->>'high')::NUMERIC AS high,
    (item->>'low')::NUMERIC AS low,
    (item->>'close')::NUMERIC AS close,
    (item->>'volume')::NUMERIC AS volume,
    (item->>'trade_value')::NUMERIC AS trade_value,
    (item->>'transaction_count')::NUMERIC AS transaction_count,
    (item->>'change')::NUMERIC AS change
FROM {{ schema }}.raw_ohlcv_daily_tpex
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(ohlcv->'rows', '[]'::jsonb)) AS item;
