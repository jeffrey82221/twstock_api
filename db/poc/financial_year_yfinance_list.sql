-- financial_year_yfinance_list
-- 上游：company_basic_info_list
-- 用途：yfinance 版年頻財報事件母體，與 FinMind 版 financial_year_list 分流。
-- 設計理念（rule 20）：不同資料源分流爬取 —— yfinance rate limit 遠寬於
-- FinMind（yfinance 每小時可達數千次；FinMind 有 daily quota），兩者
-- 共用同一個 seed 會讓快的被慢的拖住。拆成兩張 _list 各自對應獨立的
-- pop.<seed> 空表，可各自以 doubling limit 增量填充,互不阻塞。
--
-- 邊界（rule 13：對齊資料源實際覆蓋）：yfinance Ticker.quarterly_financials 只保留最近幾季
--   （滾動視窗，與 as_of 無關），backend 再依 as_of 篩「季底 <= as_of」。實測 pop 300 列中只有
--   最新一年（2026-01-01）的 4 列帶回季度，2025-01-01 以前全部是空陣列。因此只產生「今年 1/1」
--   這一個 as_of；每跨一年 generate_series 自然多出新的一列，已爬過的舊年度列留在 pop 不受影響。
--   與 financial_year_list 的差別僅在這個下限（yfinance 無長期歷史）。
-- rule 13 例外：`incorporation_date IS NOT NULL` 為技術性 guard（GREATEST 會忽略 NULL）。
SELECT
	stk_code,
	generate_series(
	    GREATEST(custom.trunc_year(incorporation_date), custom.trunc_year(CURRENT_DATE)),
		CURRENT_DATE,
		INTERVAL '1 year'
	)::DATE AS year_start_date
FROM {{ schema }}.company_basic_info_list
WHERE incorporation_date IS NOT NULL;
