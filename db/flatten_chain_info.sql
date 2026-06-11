
CREATE or REPLACE VIEW investment.flatten_chain_info AS
SELECT
	t.ic_code,
	t.ic_name,
	t.segment_key,
	t.top_code,
	t.top_name,
    sub_elem->>'sub_code'  AS sub_code,
    sub_elem->>'sub_name'  AS sub_name,
    comp->>'stk_code'      AS stk_code,
    comp->>'name'          AS company_name
FROM (
	SELECT
	    tt.segments->>'ic_code'      AS ic_code,
	    tt.segments->>'ic_name'      AS ic_name,
	    seg.key               AS segment_key,
	    top_elem->>'top_code' AS top_code,
	    top_elem->>'top_name' AS top_name,
		(top_elem->>'sub_chains') AS subchains
	FROM investment.raw_chain_info tt
	CROSS JOIN LATERAL jsonb_each(tt.segments->'segments') AS seg(key, value)
	CROSS JOIN LATERAL jsonb_array_elements(seg.value) AS top_elem
) t
CROSS JOIN LATERAL jsonb_array_elements(t.subchains::JSONB) AS sub_elem
CROSS JOIN LATERAL jsonb_array_elements(sub_elem->'companies') AS comp