-- ohlcv_daily_twse
-- 上游：raw_ohlcv_daily_twse（每 (stk_code, month_start_date) 一列，內含該月日 K JSON 陣列）
-- 用途：把 TWSE STOCK_DAY payload 攤平成 (trade_date, stk_code) 逐股逐日正規化行情。
--
-- 設計理念（rule 16 · pg_ivm 相容）：只用 CROSS JOIN LATERAL jsonb_array_elements(ohlcv->'data')
--   攤平陣列成逐日一列，無 LEFT/OUTER JOIN、無 window、無 GROUP BY、無 CTE、無 DISTINCT。
--
-- 設計理念（rule 12 · 民國年 → 西元）：TWSE data[*][0] 為民國年字串（如 '115/06/01'）；
--   採用與 product_revenue_filer_list 同 pattern 的 make_date + SUBSTRING 展開，全部 IMMUTABLE。
--
-- 設計理念（rule 13 · 不過濾）：保留 payload 每列（含 Change=' 0.00' 平盤日、註記為空等）。
--   若 stat != 'OK' 則 data 為 [] → LATERAL 自然攤平 0 列，無需 WHERE。
-- 設計理念（NULL-safe）：用 COALESCE(ohlcv->'data', '[]'::jsonb) 防止 payload 缺 data key 時
--   炸掉整個 view refresh（rule 13 精神：不動邏輯，只做結構性 guard）。
--
-- 欄位對齊 ohlcv_daily_tpex：market='sii'（上市）；volume/trade_value/transaction_count 命名
--   對齊 TPEx 版，方便下游 UNION 全市場。
--
-- TWSE data[*] 欄位順序（fields 給定）：
--   [0]日期  [1]成交股數  [2]成交金額  [3]開盤價  [4]最高價  [5]最低價
--   [6]收盤價  [7]漲跌價差  [8]成交筆數  [9]註記
-- 數字欄位含千分位逗號與可能的前導空白/正負號，一律 REPLACE(...,',','') 後 CAST；
--   漲跌欄位可能為 ' 0.00' / '+40.00' / '-65.00'，額外 TRIM(LEADING '+' FROM ...) 去正號。
SELECT
    make_date(
        SUBSTRING(item->>0, 1, 3)::INT + 1911,
        SUBSTRING(item->>0, 5, 2)::INT,
        SUBSTRING(item->>0, 8, 2)::INT
    ) AS trade_date,
    stk_code,
    'sii'::TEXT AS market,
    REPLACE(item->>3, ',', '')::NUMERIC AS open,
    REPLACE(item->>4, ',', '')::NUMERIC AS high,
    REPLACE(item->>5, ',', '')::NUMERIC AS low,
    REPLACE(item->>6, ',', '')::NUMERIC AS close,
    REPLACE(item->>1, ',', '')::NUMERIC AS volume,
    REPLACE(item->>2, ',', '')::NUMERIC AS trade_value,
    REPLACE(item->>8, ',', '')::NUMERIC AS transaction_count,
    TRIM(LEADING '+' FROM REPLACE(TRIM(item->>7), ',', ''))::NUMERIC AS change
FROM {{ schema }}.raw_ohlcv_daily_twse
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(ohlcv->'data', '[]'::jsonb)) AS item;
