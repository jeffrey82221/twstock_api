-- ohlcv_daily_twse
-- 上游：raw_ohlcv_daily_twse（每 (stk_code, month_start_date) 一列，內含該月日 K JSON）
-- 用途：把 /api/ohlcv payload 攤平成 (trade_date, stk_code) 逐股逐日正規化行情。
--
-- 設計理念（rule 16 · pg_ivm 相容）：只用 CROSS JOIN LATERAL jsonb_array_elements(ohlcv->'rows')
--   攤平陣列成逐日一列，無 LEFT/OUTER JOIN、無 window、無 GROUP BY、無 CTE、無 DISTINCT。
--
-- 設計理念（rule 12 · 民國年 → 西元）：/api/ohlcv 已由 backend 統一轉為西元 ISO 'YYYY-MM-DD'，
--   normalized 層不再需要 make_date + SUBSTRING 展開民國年，直接 CAST 為 DATE。
--
-- 設計理念（rule 13 · 不過濾）：保留 payload 每列。found=false 或 rows=[] → LATERAL 自然攤平 0 列。
-- 設計理念（NULL-safe）：COALESCE(ohlcv->'rows', '[]'::jsonb) 防 payload 缺 rows key 時炸掉。
--
-- 欄位對齊 ohlcv_daily_tpex：market='sii'（上市）；欄位命名與 TPEx 版完全一致，方便下游 UNION 全市場。
--
-- rows[*] 欄位（backend 已正規化，直接對映）：
--   trade_date (str 'YYYY-MM-DD')  stk_code (str)  open/high/low/close (float)
--   volume (float, 股)  trade_value (float, 元)  transaction_count (float)  change (float, signed)
-- 無千分位逗號、無民國年、無 signed prefix 需要清洗。
SELECT
    (item->>'trade_date')::DATE AS trade_date,
    item->>'stk_code' AS stk_code,
    'sii'::TEXT AS market,
    (item->>'open')::NUMERIC AS open,
    (item->>'high')::NUMERIC AS high,
    (item->>'low')::NUMERIC AS low,
    (item->>'close')::NUMERIC AS close,
    (item->>'volume')::NUMERIC AS volume,
    (item->>'trade_value')::NUMERIC AS trade_value,
    (item->>'transaction_count')::NUMERIC AS transaction_count,
    (item->>'change')::NUMERIC AS change
FROM {{ schema }}.raw_ohlcv_daily_twse
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(ohlcv->'rows', '[]'::jsonb)) AS item;
