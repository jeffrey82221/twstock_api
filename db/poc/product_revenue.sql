-- product_revenue
-- 上游：raw_product_revenue（每列 = 該公司某申報月的一次 filing）
-- 攤平：每行 = 該公司某申報月的某一項產品
-- 欄位 align ProductRevenueItem + ProductRevenueResponse
--
-- 設計理念（rule 15）：raw 表已由 product_revenue_filer_list（事件母體）驅動，
--   每列 (stk_code, report_month) 都是真實有申報的事件；不需從 JSON 民國年月重新合成 report_month。
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows；用 LEFT JOIN LATERAL
--                    讓 items=null / 空 array 的公司仍保留一列（產品欄位為 NULL）。
SELECT
    stk_code,
    ym,
    report_month,
    TRIM('"' FROM (product_revenue->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (product_revenue->>'company_name')) AS company_name,
    (product_revenue->>'sales_return')::NUMERIC AS sales_return,
    (product_revenue->>'total_revenue')::NUMERIC AS total_revenue,
    TRIM('"' FROM (item->>'rank')) AS rank,
    TRIM('"' FROM (item->>'name')) AS name,
    (item->>'amount')::NUMERIC AS amount,
    (item->>'percentage')::NUMERIC AS percentage
FROM {{ schema }}.raw_product_revenue
INNER JOIN LATERAL jsonb_array_elements(COALESCE(product_revenue->'items', '[]'::jsonb)) AS item ON TRUE
