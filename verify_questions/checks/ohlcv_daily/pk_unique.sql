-- 維度：uniqueness（唯一性）
-- 斷言：(stk_code, trade_date) 不重複。回傳重複的鍵，0 列 = PASS。
-- 參數：{{ stk_codes }} 例 '2330','2317'
-- 2026-11-24 全表基線：0 違規
SELECT stk_code, trade_date, count(*) AS n
FROM pop.ohlcv_daily
WHERE stk_code IN ({{ stk_codes }})
GROUP BY 1, 2
HAVING count(*) > 1
