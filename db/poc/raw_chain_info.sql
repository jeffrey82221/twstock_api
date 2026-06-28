SELECT 
	ic_code,
	ic_name,
	'http://host.docker.internal:5002/api/chain/' || ic_code AS url,
	custom.http_get_content(
		('http://host.docker.internal:5002/api/chain/' || ic_code)::TEXT
	) AS segments
FROM {{ schema }}.chain_list;