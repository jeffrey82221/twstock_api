-- ohlcv_daily_tpex
-- 上游：raw_ohlcv_daily_tpex（每 (stk_code, month_start_date) 一列，內含該月日 K JSON）
-- 用途：把 TPEx tradingStock payload 攤平成 (trade_date, stk_code) 逐股逐日正規化行情。
--
-- 設計理念（rule 16 · pg_ivm 相容）：只用 CROSS JOIN LATERAL
--   jsonb_array_elements(ohlcv->'tables'->0->'data') 攤平陣列成逐日一列。
-- 設計理念（rule 12 · 民國年 → 西元）：TPEx data[*][0] 為民國年字串（如 '115/06/01'），同
--   TWSE pattern 展開。
-- 設計理念（rule 13）：不做業務過濾；stat != 'ok' 或個股該月未上櫃 → data=[] → LATERAL 0 列。
-- 設計理念（NULL-safe）：COALESCE 防止 tables 缺失或空陣列導致 refresh 中斷。
--
-- 欄位對齊 ohlcv_daily_twse：market='otc'（上櫃）；欄位命名、單位皆對齊 TWSE 版。
--
-- TPEx data[*] 欄位順序（fields 給定，注意單位）：
--   [0]日 期  [1]成交張數(*1000=股)  [2]成交仟元(*1000=元)
--   [3]開盤   [4]最高   [5]最低   [6]收盤   [7]漲跌   [8]筆數
-- 單位換算：TPEx 成交量單位為「張」(1 張 = 1000 股)，TWSE 為「股」→ TPEx 一律 * 1000 對齊
--          TPEx 成交金額單位為「仟元」，TWSE 為「元」→ TPEx 一律 * 1000 對齊
-- 漲跌欄位：TPEx 為 '-65.00' / '10.00' 純數字（不含前導 +），仍以 TRIM(LEADING '+') 幫防禦。
SELECT
    make_date(
        SUBSTRING(item->>0, 1, 3)::INT + 1911,
        SUBSTRING(item->>0, 5, 2)::INT,
        SUBSTRING(item->>0, 8, 2)::INT
    ) AS trade_date,
    stk_code,
    'otc'::TEXT AS market,
    REPLACE(item->>3, ',', '')::NUMERIC AS open,
    REPLACE(item->>4, ',', '')::NUMERIC AS high,
    REPLACE(item->>5, ',', '')::NUMERIC AS low,
    REPLACE(item->>6, ',', '')::NUMERIC AS close,
    (REPLACE(item->>1, ',', '')::NUMERIC * 1000) AS volume,
    (REPLACE(item->>2, ',', '')::NUMERIC * 1000) AS trade_value,
    REPLACE(item->>8, ',', '')::NUMERIC AS transaction_count,
    TRIM(LEADING '+' FROM REPLACE(TRIM(item->>7), ',', ''))::NUMERIC AS change
FROM {{ schema }}.raw_ohlcv_daily_tpex
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(ohlcv->'tables'->0->'data', '[]'::jsonb)
) AS item;
