CREATE OR REPLACE VIEW investment.stk_company_info
AS
SELECT 
	stk_code,
	company_name,
	(SELECT content FROM http_get('http://host.docker.internal:5001/api/company/' || stk_code || '/basic'))::JSONB AS basic,
	(SELECT content FROM http_get('http://host.docker.internal:5001/api/company/' || stk_code || '/business-items'))::JSONB AS business_items
FROM (
SELECT DISTINCT stk_code, company_name 
FROM investment.flatten_chain_info
);