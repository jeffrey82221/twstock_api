SELECT 
	stk_code, 
	make_date(
		EXTRACT(YEAR FROM incorporation_date)::int + offset_years,
		1,
		1
	) AS year_start_date
FROM {{ schema }}.company_basic_info
CROSS JOIN LATERAL generate_series(
	0,
	(EXTRACT(YEAR FROM CURRENT_DATE)::int - EXTRACT(YEAR FROM incorporation_date)::int)
) AS gs(offset_years);
