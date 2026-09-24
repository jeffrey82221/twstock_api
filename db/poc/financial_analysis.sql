-- financial_analysis
-- 上游：raw_financial_analysis
-- 欄位 align FinancialAnalysisResponse + FinancialAnalysisData（t51sb02 21 欄財務比率）
-- 主鍵：(stk_code, year)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows（含 found=false 的列）。
-- 型別安全：numeric 欄位一律走 `->>`（回傳 text）再 `::NUMERIC`，jsonb null 安全轉為 SQL NULL。
SELECT
    stk_code,
    year,
    financial_analysis->>'stock_id' AS stock_id,
    financial_analysis->>'reason' AS reason,
    financial_analysis->'data'->>'company_name' AS company_name,
    financial_analysis->'data'->>'market' AS market,
    (financial_analysis->'data'->>'debt_ratio')::NUMERIC AS debt_ratio,
    (financial_analysis->'data'->>'lt_fund_to_ppe_ratio')::NUMERIC AS lt_fund_to_ppe_ratio,
    (financial_analysis->'data'->>'current_ratio')::NUMERIC AS current_ratio,
    (financial_analysis->'data'->>'quick_ratio')::NUMERIC AS quick_ratio,
    (financial_analysis->'data'->>'interest_coverage')::NUMERIC AS interest_coverage,
    (financial_analysis->'data'->>'ar_turnover')::NUMERIC AS ar_turnover,
    (financial_analysis->'data'->>'avg_collection_days')::NUMERIC AS avg_collection_days,
    (financial_analysis->'data'->>'inventory_turnover')::NUMERIC AS inventory_turnover,
    (financial_analysis->'data'->>'avg_sales_days')::NUMERIC AS avg_sales_days,
    (financial_analysis->'data'->>'fixed_asset_turnover')::NUMERIC AS fixed_asset_turnover,
    (financial_analysis->'data'->>'total_asset_turnover')::NUMERIC AS total_asset_turnover,
    (financial_analysis->'data'->>'roa')::NUMERIC AS roa,
    (financial_analysis->'data'->>'roe')::NUMERIC AS roe,
    (financial_analysis->'data'->>'pretax_profit_to_capital_ratio')::NUMERIC AS pretax_profit_to_capital_ratio,
    (financial_analysis->'data'->>'net_profit_margin')::NUMERIC AS net_profit_margin,
    (financial_analysis->'data'->>'eps')::NUMERIC AS eps,
    (financial_analysis->'data'->>'cash_flow_ratio')::NUMERIC AS cash_flow_ratio,
    (financial_analysis->'data'->>'cash_flow_adequacy_ratio')::NUMERIC AS cash_flow_adequacy_ratio,
    (financial_analysis->'data'->>'cash_reinvestment_ratio')::NUMERIC AS cash_reinvestment_ratio
FROM {{ schema }}.raw_financial_analysis
