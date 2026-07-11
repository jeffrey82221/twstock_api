SELECT 
	stk_code,
	company_name,
	basic->'error' AS error,
	(basic->'found')::BOOL AS found,
	BTRIM((basic->'market')::TEXT, '"'::TEXT) AS market,
	BTRIM((basic->'tax_id')::TEXT, '"'::TEXT) AS tax_id,
	BTRIM((basic->'address')::TEXT, '"'::TEXT) AS address,
	BTRIM((basic->'website')::TEXT, '"'::TEXT) AS website,
	BTRIM((basic->'chairman')::TEXT, '"'::TEXT) AS chairman,
	BTRIM((basic->'stock_id')::TEXT, '"'::TEXT) AS stock_id,
	BTRIM((basic->'short_name')::TEXT, '"'::TEXT) AS short_name,
	BTRIM((basic->'company_name')::TEXT, '"'::TEXT) AS full_name,
	BTRIM((basic->'english_name')::TEXT, '"'::TEXT) AS english_name,
	custom.parse_iso_date(BTRIM((basic->'listing_date')::TEXT, '"'::TEXT)) AS listing_date,
	BTRIM((basic->'industry_code')::TEXT, '"'::TEXT) AS industry_code,
	BTRIM((basic->'industry_name')::TEXT, '"'::TEXT) AS industry_name,
	BTRIM((basic->'general_manager')::TEXT, '"'::TEXT) AS general_manager,
	CASE WHEN BTRIM((basic->'paid_in_capital')::TEXT, '"'::TEXT) ~ '^[0-9]+$' THEN
		BTRIM((basic->'paid_in_capital')::TEXT, '"'::TEXT)::BIGINT
	ELSE NULL END AS paid_in_capital,
	custom.parse_iso_date(BTRIM((basic->'incorporation_date')::TEXT, '"'::TEXT)) AS incorporation_date
FROM {{ schema }}.raw_company_info