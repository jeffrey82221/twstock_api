-- 維度：cross-source（跨來源一致）
-- 斷言：估值的收盤價（TWSE BWIBBU_d）= 同日日 K 收盤價（STOCK_DAY／TPEx），誤差 1%。
-- 回傳違規列，0 列 = PASS；另一欄 comparable_rows 為可比對列數，= 0 時結果為 INCONCLUSIVE。
-- 參數：{{ stk_codes }} 例 '2330','2317'
-- 2026-11-24 全表基線：543 列可比對、0 違規
WITH j AS (
    SELECT v.stk_code, v.data_date, v.close_price, o.close
    FROM pop.company_valuation v
    JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
    WHERE v.stk_code IN ({{ stk_codes }})
)
SELECT stk_code, data_date, close_price, close, (SELECT count(*) FROM j) AS comparable_rows
FROM j
WHERE abs(close_price - close) > 0.01 * close
