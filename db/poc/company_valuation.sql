-- company_valuation
-- 上游：raw_company_valuation
-- 欄位 align CompanyValuationResponse + MarketValuationConstituent
-- 主鍵：(stk_code, as_of)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體所有 rows（含 found=false 的非交易日列）。
SELECT
    stk_code,
    custom.parse_iso_date(company_valuation->>'date') AS as_of,
    company_valuation->>'stock_id' AS stock_id,
    company_valuation->>'reason' AS reason,
    company_valuation->>'calculation_method' AS calculation_method,
    company_valuation->'constituent'->>'stock_name' AS stock_name,
    (company_valuation->'constituent'->>'close_price')::NUMERIC AS close_price,
    (company_valuation->'constituent'->>'per')::NUMERIC AS per,
    (company_valuation->'constituent'->>'pbr')::NUMERIC AS pbr,
    (company_valuation->'constituent'->>'dividend_yield_pct')::NUMERIC AS dividend_yield_pct,
    (company_valuation->'constituent'->>'estimated_shares')::NUMERIC AS estimated_shares,
    (company_valuation->'constituent'->>'market_cap')::NUMERIC AS market_cap,
    (company_valuation->'constituent'->>'eps_ttm')::NUMERIC AS eps_ttm,
    (company_valuation->'constituent'->>'bvps')::NUMERIC AS bvps,
    (company_valuation->'constituent'->>'dps')::NUMERIC AS dps
FROM {{ schema }}.raw_company_valuation
