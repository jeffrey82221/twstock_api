-- product_revenue
-- 上游：raw_product_revenue
-- 攤平：每行 = 該公司最後一次申報期中的某一項產品
-- 欄位 align ProductRevenueItem + ProductRevenueResponse
-- 設計理念（rule 12）：民國年 + 月份 → 西元年月日 DATE。
--                    西元年 = 民國年 + 1911。當年/月為 NULL 時 report_month 亦為 NULL。
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows；用 LEFT JOIN LATERAL
--                    讓 items=null / 空 array 的公司仍保留一列（產品欄位為 NULL）。
SELECT
    stk_code,
    TRIM('"' FROM (product_revenue->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (product_revenue->>'company_name')) AS company_name,
    CASE
        WHEN (product_revenue->>'year') IS NULL OR (product_revenue->>'month') IS NULL THEN NULL
        ELSE make_date(
            (product_revenue->>'year')::INT + 1911,
            (product_revenue->>'month')::INT,
            1
        )
    END AS report_month,
    (product_revenue->>'sales_return')::NUMERIC AS sales_return,
    (product_revenue->>'total_revenue')::NUMERIC AS total_revenue,
    TRIM('"' FROM (item->>'rank')) AS rank,
    TRIM('"' FROM (item->>'name')) AS name,
    (item->>'amount')::NUMERIC AS amount,
    (item->>'percentage')::NUMERIC AS percentage
FROM {{ schema }}.raw_product_revenue
LEFT JOIN LATERAL jsonb_array_elements(COALESCE(product_revenue->'items', '[]'::jsonb)) AS item ON TRUE
