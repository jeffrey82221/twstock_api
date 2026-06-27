SELECT 
	stk_code, 
	generate_series(
	    {{ schema }}.trunc_year(incorporation_date),
		CURRENT_DATE,        
		INTERVAL '1 year'
	) AS year_start_date
FROM {{ schema }}.company_basic_info;