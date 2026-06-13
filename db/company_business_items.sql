CREATE or REPLACE VIEW investment.company_business_items
AS
SELECT 
    business_items->>'stock_id' AS stock_id,
    elem->>'code' AS code,
    elem->>'desc' AS desc
FROM (
	SELECT *
	FROM
    	investment.raw_company_info
	WHERE (business_items->'found')::BOOL
)
CROSS JOIN LATERAL jsonb_array_elements(business_items->'categories') AS elem