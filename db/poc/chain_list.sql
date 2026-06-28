SELECT 
    jsonb_array_elements(chains::jsonb)->>'ic_code' AS ic_code,
    jsonb_array_elements(chains::jsonb)->>'ic_name' AS ic_name
FROM (
    SELECT custom.http_get_content('http://host.docker.internal:5002/api/chains')::jsonb->>'chains' AS chains
);