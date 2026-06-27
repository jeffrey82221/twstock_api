SELECT 
	stk_code,
	value::DATE AS quater
FROM (

	SELECT 
		stk_code,
		quarter
	FROM (
	SELECT 
		stk_code,
		(
	    SELECT jsonb_agg(DISTINCT elem)
		    FROM (
		        SELECT jsonb_array_elements(financials->'eps'->'ttm_quarters') AS elem
		        UNION
		        SELECT jsonb_array_elements(financials->'net_income'->'ttm_quarters') AS elem
		    ) sub
		) AS quarter
	FROM {{ schema }}.raw_yearly_financials
	)
	WHERE quarter <> '[null]'
) AS t
CROSS JOIN LATERAL jsonb_array_elements_text(t.quarter) AS elem(value)