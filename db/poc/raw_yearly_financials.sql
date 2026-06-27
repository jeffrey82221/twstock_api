SELECT 
	stk_code,
	{{ schema }}.http_get_content(
		('http://host.docker.internal:5002/api/company/' || stk_code || '/financials?as_of=' || year_start_date::DATE )::TEXT
	) AS financials,
	year_start_date AS as_of
FROM {{ schema }}.financial_year_list
