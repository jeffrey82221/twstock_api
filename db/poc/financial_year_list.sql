SELECT 
	stk_code, 
	generate_series(
	    DATE_TRUNC('year', incorporation_date)::DATE,
		CURRENT_DATE,        
		INTERVAL '1 year'
	) AS year_start_date
FROM {{ schema }}.company_basic_info;
