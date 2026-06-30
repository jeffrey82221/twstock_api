-- product_revenue
-- 上游：raw_product_revenue
-- 攤平：每行 = 該公司最後一次申報期中的某一項產品
-- 欄位 align ProductRevenueItem + ProductRevenueResponse
SELECT
    stk_code,
    TRIM('"' FROM (product_revenue->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (product_revenue->>'company_name')) AS company_name,
    TRIM('"' FROM (product_revenue->>'year')) AS year,
    TRIM('"' FROM (product_revenue->>'month')) AS month,
    TRIM('"' FROM (item->>'rank')) AS rank,
    TRIM('"' FROM (item->>'name')) AS name,
    (item->>'amount')::NUMERIC AS amount,
    (item->>'percentage')::NUMERIC AS percentage
FROM {{ schema }}.raw_product_revenue,
     LATERAL jsonb_array_elements(COALESCE(product_revenue->'items', '[]'::jsonb)) AS item
WHERE (product_revenue->>'found')::BOOLEAN = TRUE
