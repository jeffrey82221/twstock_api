SELECT 
	stk_code,
	TO_DATE(TRIM('"' FROM (financials->'as_of')::TEXT), 'YYYY-MM-DD') AS as_of,
	TRIM('"' FROM (financials->'stock_id')::TEXT) AS stock_id,
	(financials->'eps'->'ttm')::NUMERIC AS eps_ttm,
	TO_DATE(TRIM('"' FROM (financials->'eps'->'latest_quarter_date')::TEXT), 'YYYY-MM-DD') AS latest_quarter_date,
	(financials->'eps'->'latest_quarter_value')::NUMERIC AS latest_quarter_eps,
	(financials->'net_income'->'ttm')::NUMERIC AS net_income_ttm,
	(financials->'net_income'->'latest_quarter_value')::NUMERIC AS latest_quarter_net_income,
	(financials->'operating_margin_pct')::NUMERIC AS operating_margin_pct,
	(financials->>'revenue_ttm_from_financial_statements')::NUMERIC AS revenue_ttm
FROM (
	SELECT *
	FROM (
		SELECT 
			stk_code,
			(SELECT content FROM http_get('http://host.docker.internal:5001/api/company/' || stk_code || '/financials?as_of=' || date_id ))::JSONB AS financials
		FROM (
			SELECT 
				'2330' AS stk_code,
			    generated_date::date AS date_id
			FROM 
			    generate_series(
			        '2025-01-01'::date,
			        CURRENT_DATE,        
			        INTERVAL '1 day'
			    ) AS generated_date
			LIMIT 3
		)
	)
	WHERE (financials->'eps'->'ttm')::text <> 'null'
)