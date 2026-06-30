-- monthly_revenue
-- 上游：raw_monthly_revenue
-- 欄位 align RevenueResponse + financial_quarterly 既有欄位命名規則（`as_of`、`stock_id`、`stk_code`）
SELECT
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (revenue->'as_of')::TEXT)) AS as_of,
    TRIM('"' FROM (revenue->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (revenue->>'latest_month_label')) AS latest_month_label,
    (revenue->>'latest_month_value')::NUMERIC AS latest_month_value,
    (revenue->>'latest_month_yoy_pct')::NUMERIC AS latest_month_yoy_pct,
    (revenue->>'ttm_value')::NUMERIC AS ttm_value,
    (revenue->>'ttm_yoy_pct')::NUMERIC AS ttm_yoy_pct
FROM {{ schema }}.raw_monthly_revenue
WHERE (revenue->>'found')::BOOLEAN = TRUE
  AND (revenue->'latest_month_value')::TEXT <> 'null'
