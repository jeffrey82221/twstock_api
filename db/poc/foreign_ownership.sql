-- foreign_ownership
-- 上游：raw_foreign_ownership
-- 欄位 align ForeignOwnershipResponse + ForeignOwnershipRow
-- 主鍵顯示規則與 monthly_revenue 同 (`stk_code` + `as_of`)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows（含 found=false 的列）。
SELECT
    stk_code,
    custom.parse_iso_date(foreign_ownership->>'as_of') AS as_of,
    foreign_ownership->>'stock_id' AS stock_id,
    custom.parse_iso_date(foreign_ownership->>'data_date') AS data_date,
    foreign_ownership->'row'->>'stock_name' AS stock_name,
    (foreign_ownership->'row'->>'issued_shares')::NUMERIC AS issued_shares,
    (foreign_ownership->'row'->>'foreign_investment_available_shares')::NUMERIC AS foreign_investment_available_shares,
    (foreign_ownership->'row'->>'foreign_investment_shares')::NUMERIC AS foreign_investment_shares,
    (foreign_ownership->'row'->>'foreign_investment_available_ratio_pct')::NUMERIC AS foreign_investment_available_ratio_pct,
    (foreign_ownership->'row'->>'foreign_investment_ratio_pct')::NUMERIC AS foreign_investment_ratio_pct,
    (foreign_ownership->'row'->>'foreign_investment_limit_ratio_pct')::NUMERIC AS foreign_investment_limit_ratio_pct,
    (foreign_ownership->'row'->>'china_investment_limit_ratio_pct')::NUMERIC AS china_investment_limit_ratio_pct,
    foreign_ownership->'row'->>'change_reason' AS change_reason,
    custom.parse_iso_date(foreign_ownership->'row'->>'last_change_date') AS last_change_date
FROM {{ schema }}.raw_foreign_ownership
