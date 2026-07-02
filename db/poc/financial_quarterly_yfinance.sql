SELECT 
	stk_code,
	custom.parse_iso_date(TRIM('"' FROM (financials->'as_of')::TEXT)) AS as_of,
	TRIM('"' FROM (financials->'stock_id')::TEXT) AS stock_id,
	CASE WHEN (financials->'eps'->'ttm')::text = 'null' THEN NULL
		ELSE
	(financials->'eps'->'ttm')::NUMERIC 
	END
	AS eps_ttm,
	custom.parse_iso_date(TRIM('"' FROM (financials->'eps'->'latest_quarter_date')::TEXT)) AS latest_quarter_date,
	(financials->'eps'->'latest_quarter_value')::NUMERIC AS latest_quarter_eps,
	(financials->'net_income'->>'ttm')::NUMERIC AS net_income_ttm,
	(financials->'net_income'->>'latest_quarter_value')::NUMERIC AS latest_quarter_net_income,
	(financials->>'operating_margin_pct')::NUMERIC AS operating_margin_pct,
	(financials->>'revenue_ttm_from_financial_statements')::NUMERIC  AS revenue_ttm
FROM (
	SELECT *
	FROM (
		SELECT 
			stk_code,
			custom.http_get_content(
				(
					'http://host.docker.internal:5002/api/company/' || stk_code || '/financials/yfinance?as_of=' || custom.date_to_iso(quater)
				)::TEXT
				) AS financials
		FROM {{ schema }}.financial_quarter_yfinance_list
	)
	WHERE (financials->'eps'->'ttm')::text <> 'null'
)
