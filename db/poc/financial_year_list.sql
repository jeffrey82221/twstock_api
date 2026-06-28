SELECT 
	stk_code, 
	generate_series(
	    custom.trunc_year(incorporation_date),
		CURRENT_DATE,        
		INTERVAL '1 year'
	)::DATE AS year_start_date
FROM {{ schema }}.company_basic_info;