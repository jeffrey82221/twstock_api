-- monthly_revenue
-- 上游：raw_monthly_revenue
-- 欄位 align RevenueResponse + 既有 view 命名規則（`as_of`、`stock_id`、`stk_code`）
-- 設計理念（rule 13）：不做 WHERE 過濾，讓 raw_monthly_revenue 每列（含 found=false / 值為 null）都攤平出來，
--                    保留 full scan；下游需要時再自行過濾 NULL。
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
