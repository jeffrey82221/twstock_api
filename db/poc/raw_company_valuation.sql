-- raw_company_valuation
-- 上游：company_valuation_monthly_list（每公司每月一筆 month_start_date）
-- 對應 endpoint: GET /api/company-valuation?stock_id={stk_code}&date={month_start_date}
-- 上游資料源：TWSE 收盤後個股本益比、殖利率及股價淨值比 BWIBBU_d
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company-valuation?stock_id=' || stk_code
            || '&date=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS company_valuation,
    month_start_date AS as_of
FROM {{ schema }}.company_valuation_monthly_list
