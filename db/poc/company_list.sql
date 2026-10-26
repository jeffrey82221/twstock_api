-- 公司清單（跨產業鏈去重）。
-- 不使用 SELECT DISTINCT：DISTINCT 會強迫 Postgres 先展開全部 47 條產業鏈
-- (47 次 HTTP) 才吐得出第一列，使 LIMIT 1 / WHERE 無法提早結束。
-- 改以 recursive CTE 逐條產業鏈走訪，只輸出「尚未出現過」的公司，
-- 讓 LIMIT 在第一條有公司的產業鏈處即可停止。
SELECT
	elem->>'stk_code' AS stk_code,
	elem->>'company_name' AS company_name
FROM (
	WITH RECURSIVE chains AS (
		SELECT ic_code, ROW_NUMBER() OVER (ORDER BY ic_code) AS n
		FROM {{ schema }}.chain_list
	),
	walk AS (
		SELECT 0::BIGINT AS n, '{}'::TEXT[] AS seen, '[]'::JSONB AS fresh
		UNION ALL
		SELECT
			c.n,
			w.seen || f.codes,
			f.fresh
		FROM walk w
		JOIN chains c ON c.n = w.n + 1
		CROSS JOIN LATERAL (
			SELECT custom.http_get_content(
				('http://host.docker.internal:5002/api/chain/' || c.ic_code)::TEXT
			) AS j
		) r
		CROSS JOIN LATERAL (
			SELECT
				COALESCE(ARRAY_AGG(x.stk_code), '{}'::TEXT[]) AS codes,
				COALESCE(JSONB_AGG(JSONB_BUILD_OBJECT('stk_code', x.stk_code, 'company_name', x.company_name)), '[]'::JSONB) AS fresh
			FROM (
				SELECT comp->>'stk_code' AS stk_code, MIN(comp->>'name') AS company_name
				FROM JSONB_EACH(r.j->'segments') AS seg(key, value)
				CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS(seg.value) AS top_elem
				CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS((top_elem->>'sub_chains')::JSONB) AS sub_elem
				CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS(sub_elem->'companies') AS comp
				WHERE NOT (comp->>'stk_code' = ANY(w.seen))
				GROUP BY comp->>'stk_code'
			) x
		) f
	)
	SELECT fresh FROM walk
) t
CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS(t.fresh) AS elem
