-- monthly_revenue_twse
-- 上游：raw_monthly_revenue_twse
-- 欄位完全與 monthly_revenue (FinMind 版) align，差別只在資料源 (TWSE/MOPS vs FinMind)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows。
SELECT
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (revenue->'as_of')::TEXT)) AS as_of,
    TRIM('"' FROM (revenue->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (revenue->>'latest_month_label')) AS latest_month_label,
    (revenue->>'latest_month_value')::NUMERIC AS latest_month_value,
    (revenue->>'latest_month_yoy_pct')::NUMERIC AS latest_month_yoy_pct,
    (revenue->>'ttm_value')::NUMERIC AS ttm_value,
    (revenue->>'ttm_yoy_pct')::NUMERIC AS ttm_yoy_pct
FROM {{ schema }}.raw_monthly_revenue_twse
