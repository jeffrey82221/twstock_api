-- financial_year_yfinance_list
-- 上游：無外部（純 SQL generate_series × company_basic_info）
-- 用途：yfinance 版年頻財報事件母體，與 FinMind 版 financial_year_list 分流。
-- 設計理念（rule 20）：不同資料源分流爬取 —— yfinance rate limit 遠寬於
-- FinMind（yfinance 每小時可達數千次；FinMind 有 daily quota），兩者
-- 共用同一個 seed 會讓快的被慢的拖住。拆成兩張 _list 各自對應獨立的
-- pop.<seed> 空表，可各自以 doubling limit 增量填充,互不阻塞。
--
-- 內容與 financial_year_list 完全一致 —— 拆分價值僅在於獨立 seed。
SELECT
	stk_code,
	generate_series(
	    custom.trunc_year(incorporation_date),
		CURRENT_DATE,
		INTERVAL '1 year'
	)::DATE AS year_start_date
FROM {{ schema }}.company_basic_info;
