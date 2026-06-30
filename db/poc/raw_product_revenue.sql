-- raw_product_revenue
-- 上游：company_list（每家公司抓一次最新申報期）
-- 對應 endpoint: GET /api/company/{stock_id}/product-revenue（無 as_of 參數，取公司最後一次申報期）
-- 上游資料源：公開資訊觀測站 (MOPS) t05st08「各項產品業務營收統計表」
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/product-revenue')::TEXT
    ) AS product_revenue
FROM {{ schema }}.company_list
