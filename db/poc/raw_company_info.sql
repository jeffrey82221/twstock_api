SELECT 
	stk_code,
	company_name,
	{{ schema }}.http_get_content(
		('http://host.docker.internal:5002/api/company/' || stk_code || '/basic')::TEXT
	) AS basic,
	{{ schema }}.http_get_content(
		('http://host.docker.internal:5002/api/company/' || stk_code || '/business-items')::TEXT
	) AS business_items
FROM {{ schema }}.company_list;