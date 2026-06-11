CREATE OR REPLACE VIEW investment.raw_chain_info
AS
SELECT 
	ic_code,
	ic_name,
	'http://host.docker.internal:5001/api/chain/' || ic_code AS url,
	(
	SELECT content 
	FROM http_get('http://host.docker.internal:5001/api/chain/' || ic_code)
	)::jsonb 
	AS segments
FROM (
	SELECT 
		jsonb_array_elements(chains::jsonb)->>'ic_code' AS ic_code,
		jsonb_array_elements(chains::jsonb)->>'ic_name' AS ic_name
	FROM (
		SELECT
		    status,
		    content_type,
		    content::jsonb->>'chains' AS chains
		FROM http_get('http://host.docker.internal:5001/api/chains')
	)
);