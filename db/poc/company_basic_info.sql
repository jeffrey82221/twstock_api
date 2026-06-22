SELECT 
	stk_code,
	company_name
	market,
	tax_id,
	address,
	website,
	chairman,
	stock_id,
	short_name,
	full_name,
	english_name,
	listing_date::DATE,
	industry_code,
	industry_name,
	general_manager,
	paid_in_capital::BIGINT,
	incorporation_date::DATE
FROM (
	SELECT 
		stk_code,
		company_name,
		basic->'error' AS error,
		(basic->'found')::BOOL AS found,
		TRIM('"' FROM (basic->'market')::TEXT) AS market,
		TRIM('"' FROM (basic->'tax_id')::TEXT) AS tax_id,
		TRIM('"' FROM (basic->'address')::TEXT) AS address,
		TRIM('"' FROM (basic->'website')::TEXT) AS website,
		TRIM('"' FROM (basic->'chairman')::TEXT) AS chairman,
		TRIM('"' FROM (basic->'stock_id')::TEXT) AS stock_id,
		TRIM('"' FROM (basic->'short_name')::TEXT) AS short_name,
		TRIM('"' FROM (basic->'company_name')::TEXT) AS full_name,
		TRIM('"' FROM (basic->'english_name')::TEXT) AS english_name,
		TRIM('"' FROM (basic->'listing_date')::TEXT) AS listing_date,
		TRIM('"' FROM (basic->'industry_code')::TEXT) AS industry_code,
		TRIM('"' FROM (basic->'industry_name')::TEXT) AS industry_name,
		TRIM('"' FROM (basic->'general_manager')::TEXT) AS general_manager,
		TRIM('"' FROM (basic->'paid_in_capital')::TEXT) AS paid_in_capital,
		TRIM('"' FROM (basic->'incorporation_date')::TEXT) AS incorporation_date
	FROM poc.raw_company_info
)
WHERE found = TRUE;