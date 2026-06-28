SELECT 
	stk_code,
	custom.http_get_content(
		('http://host.docker.internal:5002/api/company/' || stk_code || '/financials?as_of=' || custom.date_to_iso(year_start_date)::TEXT)::TEXT
	) AS financials,
	year_start_date AS as_of
FROM {{ schema }}.financial_year_list
