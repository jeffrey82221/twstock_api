-- 維度：validity（合理性）
-- 斷言：high ≥ max(open, close)、low ≤ min(open, close)、close > 0、volume ≥ 0。回傳違規列，0 列 = PASS。
-- 參數：{{ stk_codes }} 例 '2330','2317'；{{ since }} 例 DATE '2025-11-01'
-- 2026-11-24 全表基線：0 違規
SELECT stk_code, trade_date, open, high, low, close, volume
FROM pop.ohlcv_daily
WHERE stk_code IN ({{ stk_codes }}) AND trade_date >= {{ since }}
  AND (high < GREATEST(open, close) OR low > LEAST(open, close) OR close <= 0 OR volume < 0)
