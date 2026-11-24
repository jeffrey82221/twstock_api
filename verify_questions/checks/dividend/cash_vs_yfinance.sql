-- 維度：cross-source（跨來源一致）
-- 斷言：同一 (stk_code, reference_date) 的現金股利，FinMind 與 yfinance 相差 ≤ 0.01 元。
-- 回傳違規列，0 列 = PASS。可比對列數請另跑：
--   SELECT count(*) FROM pop.dividend f JOIN pop.dividend_yfinance y USING (stk_code, reference_date)
--   WHERE f.stk_code IN ({{ stk_codes }})
-- = 0 時結果為 INCONCLUSIVE。
-- 參數：{{ stk_codes }} 例 '2330','2317'
-- 2026-11-24 全表基線：26 列可比對、0 違規
SELECT f.stk_code, f.reference_date, f.cash_dividend AS finmind_cash, y.cash_dividend AS yfinance_cash
FROM pop.dividend f
JOIN pop.dividend_yfinance y ON y.stk_code = f.stk_code AND y.reference_date = f.reference_date
WHERE f.stk_code IN ({{ stk_codes }})
  AND abs(COALESCE(f.cash_dividend, 0) - COALESCE(y.cash_dividend, 0)) > 0.01
