-- 維度：cross-source（跨來源一致）
-- 斷言：估值的收盤價（TWSE BWIBBU_d）= 同日日 K 收盤價（STOCK_DAY／TPEx），誤差 1%。
-- 回傳違規列，0 列 = PASS。可比對列數請另跑（違規查詢 0 列時無法分辨 PASS 與「根本沒有可比對列」）：
--   SELECT count(*) FROM pop.company_valuation v
--   JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
--   WHERE v.stk_code IN ({{ stk_codes }})
-- = 0 時結果為 INCONCLUSIVE。
-- 參數：{{ stk_codes }} 例 '2330','2317'
-- 2026-11-24 全表基線：543 列可比對、0 違規
SELECT v.stk_code, v.data_date, v.close_price, o.close
FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ({{ stk_codes }})
  AND abs(v.close_price - o.close) > 0.01 * o.close
