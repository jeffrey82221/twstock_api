-- company_value_chain
-- 上游：raw_company_value_chain
-- 攤平：每行 = 公司在某條鏈、某段位、某子分類下的一個 membership
-- 欄位刻意與 chain_info align：ic_code / ic_name / top_code / top_name / sub_code / sub_name / stk_code
-- 差異：以「公司視角」展開，且新增 segment（上/中/下游字串，來自 ChainMembership.segment）
SELECT
    stk_code,
    TRIM('"' FROM (m->>'ic_code')) AS ic_code,
    TRIM('"' FROM (m->>'ic_name')) AS ic_name,
    TRIM('"' FROM (m->>'segment')) AS segment,
    TRIM('"' FROM (m->>'top_code')) AS top_code,
    TRIM('"' FROM (m->>'top_name')) AS top_name,
    TRIM('"' FROM (m->>'sub_code')) AS sub_code,
    TRIM('"' FROM (m->>'sub_name')) AS sub_name
FROM {{ schema }}.raw_company_value_chain,
     LATERAL jsonb_array_elements(COALESCE(value_chain->'memberships', '[]'::jsonb)) AS m
WHERE (value_chain->>'found')::BOOLEAN = TRUE
  AND TRIM('"' FROM (value_chain->>'status')) = 'ready'
