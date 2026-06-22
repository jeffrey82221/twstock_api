SELECT 
	stk_code,
	(
	SELECT content 
	FROM http_get('http://host.docker.internal:5002/api/company/' || stk_code || '/financials?as_of=' || year_start_date::DATE ))::JSONB AS financials,
	year_start_date AS as_of
FROM poc.financial_year_list
