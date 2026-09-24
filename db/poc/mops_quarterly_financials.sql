-- mops_quarterly_financials
-- 上游：raw_mops_quarterly_financials
-- 欄位 align MopsQuarterlyFinancialsResponse + MopsQuarterlyFinancialsData
-- 主鍵顯示規則與 financial_quarterly 同 (`stk_code` + `as_of`)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows（含 found=false 的列）。
-- 型別安全：numeric 欄位一律走 `->>`（回傳 text）再 `::NUMERIC`，jsonb null 安全轉為 SQL NULL。
SELECT
    stk_code,
    custom.parse_iso_date(quarterly_financials->>'as_of') AS as_of,
    quarterly_financials->>'stock_id' AS stock_id,
    quarterly_financials->>'company_name' AS company_name,
    quarterly_financials->>'market' AS market,
    custom.parse_iso_date(quarterly_financials->>'data_date') AS data_date,
    (quarterly_financials->>'fiscal_year')::NUMERIC AS fiscal_year,
    (quarterly_financials->>'quarter')::NUMERIC AS quarter,
    (quarterly_financials->'data'->>'revenue_millions')::NUMERIC AS revenue_millions,
    (quarterly_financials->'data'->>'gross_margin_pct')::NUMERIC AS gross_margin_pct,
    (quarterly_financials->'data'->>'operating_margin_pct')::NUMERIC AS operating_margin_pct,
    (quarterly_financials->'data'->>'pretax_margin_pct')::NUMERIC AS pretax_margin_pct,
    (quarterly_financials->'data'->>'net_margin_pct')::NUMERIC AS net_margin_pct,
    (quarterly_financials->'data'->>'eps')::NUMERIC AS eps
FROM {{ schema }}.raw_mops_quarterly_financials
