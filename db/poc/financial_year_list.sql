-- financial_year_list
-- 上游：company_basic_info_list
-- 用途：FinMind 版年頻財報（/api/company/{stock_id}/financials，raw_yearly_financials）的上游母體；
--   每公司每年一筆 year_start_date（該年 1 月 1 日）。
--
-- 邊界（rule 13：對齊資料源實際覆蓋）：FinMind TaiwanStockFinancialStatements 最早一季為
--   1991-12-31（2026-11 實測 2330），as_of 早於 1992-01-01 必定拿不到任何季度，故起點取
--   GREATEST(成立年 1/1, 1992-01-01)。
-- rule 13 例外：`incorporation_date IS NOT NULL` 為技術性 guard（GREATEST 會忽略 NULL）。
SELECT
	stk_code,
	generate_series(
	    GREATEST(custom.trunc_year(incorporation_date), DATE '1992-01-01'),
		CURRENT_DATE,
		INTERVAL '1 year'
	)::DATE AS year_start_date
FROM {{ schema }}.company_basic_info_list
WHERE incorporation_date IS NOT NULL;
