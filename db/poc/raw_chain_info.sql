SELECT 
	ic_code,
	ic_name,
	'http://host.docker.internal:5002/api/chain/' || ic_code AS url,
	{{ schema }}.http_get_content(
		('http://host.docker.internal:5002/api/chain/' || ic_code)::TEXT
	) AS segments
FROM (
	SELECT 
		jsonb_array_elements(chains::jsonb)->>'ic_code' AS ic_code,
		jsonb_array_elements(chains::jsonb)->>'ic_name' AS ic_name
	FROM (
		SELECT
		    content::jsonb->>'chains' AS chains
		FROM http_get('http://host.docker.internal:5002/api/chains')
	)
);