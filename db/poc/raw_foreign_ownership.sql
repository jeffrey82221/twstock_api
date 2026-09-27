-- raw_foreign_ownership
-- 上游：foreign_ownership_monthly_list（每公司每月一筆 month_start_date）
-- 對應 endpoint: GET /api/company/{stock_id}/foreign-ownership?as_of={month_start_date}
-- 上游資料源：TWSE 外資及陸資投資持股統計 (MI_QFIIS)
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code
            || '/foreign-ownership?as_of=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS foreign_ownership,
    month_start_date AS as_of
FROM {{ schema }}.foreign_ownership_monthly_list
