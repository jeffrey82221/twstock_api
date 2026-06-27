SELECT 
    business_items->>'stock_id' AS stock_id,
    elem->>'code' AS code,
    elem->>'desc' AS desc
FROM (
	SELECT *
	FROM
    	{{ schema }}.raw_company_info
)
CROSS JOIN LATERAL jsonb_array_elements(business_items->'categories') AS elem