CREATE or REPLACE VIEW investment.financial_year_list 
AS 
SELECT 
	stk_code, 
	generate_series(
	    DATE_TRUNC('year', incorporation_date)::DATE,
		CURRENT_DATE,        
		INTERVAL '1 year'
	) AS year_start_date
FROM investment.company_basic_info;
