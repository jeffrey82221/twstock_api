-- mops_quarterly_financials
-- 上游：raw_mops_quarterly_financials
-- 欄位 align MopsQuarterlyFinancialsResponse + MopsQuarterlyFinancialsData
-- 主鍵顯示規則與 financial_quarterly 同 (`stk_code` + `as_of`)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows（含 found=false 的列）。
-- 型別安全：numeric 欄位一律走 `->>`（回傳 text）再 `::NUMERIC`，jsonb null 安全轉為 SQL NULL。
-- 主鍵（rule 7）：as_of 取 raw 的 seed 日期（該季公告期限），不取回應 JSON（I004）。
-- Point-in-time（I002）：data_date = 季末日、available_date = 法定公告期限（金融業 Q2 為 8/31），
--   endpoint 保證 available_date <= as_of。
-- 數值為**單季**：endpoint 的 `data` 已由 MOPS 年初至今累計值換算成單季（I001），累計值在 raw JSON 的 `data_ytd`。
SELECT
    stk_code,
    as_of,
    quarterly_financials->>'stock_id' AS stock_id,
    quarterly_financials->>'company_name' AS company_name,
    quarterly_financials->>'market' AS market,
    custom.parse_iso_date(quarterly_financials->>'data_date') AS data_date,
    custom.parse_iso_date(quarterly_financials->>'available_date') AS available_date,
    (quarterly_financials->>'fiscal_year')::NUMERIC AS fiscal_year,
    (quarterly_financials->>'quarter')::NUMERIC AS quarter,
    (quarterly_financials->'data'->>'revenue_millions')::NUMERIC AS revenue_millions,
    (quarterly_financials->'data'->>'gross_margin_pct')::NUMERIC AS gross_margin_pct,
    (quarterly_financials->'data'->>'operating_margin_pct')::NUMERIC AS operating_margin_pct,
    (quarterly_financials->'data'->>'pretax_margin_pct')::NUMERIC AS pretax_margin_pct,
    (quarterly_financials->'data'->>'net_margin_pct')::NUMERIC AS net_margin_pct,
    (quarterly_financials->'data'->>'eps')::NUMERIC AS eps
FROM {{ schema }}.raw_mops_quarterly_financials
