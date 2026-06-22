SELECT 
	stk_code,
	company_name,
	(SELECT content FROM http_get('http://host.docker.internal:5002/api/company/' || stk_code || '/basic'))::JSONB AS basic,
	(SELECT content FROM http_get('http://host.docker.internal:5002/api/company/' || stk_code || '/business-items'))::JSONB AS business_items
FROM poc.company_list;