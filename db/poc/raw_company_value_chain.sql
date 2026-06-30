-- raw_company_value_chain
-- 上游：company_list（每家公司一次）
-- 對應 endpoint: GET /api/company/{stock_id}/value-chain
-- 上游資料源：櫃買中心 · 產業價值鏈資訊平台 (ic.tpex.org.tw) — server-rendered HTML
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/value-chain')::TEXT
    ) AS value_chain
FROM {{ schema }}.company_list
