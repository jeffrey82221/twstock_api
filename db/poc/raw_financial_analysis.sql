-- raw_financial_analysis
-- 上游：financial_analysis_year_list（每公司每西元年一筆）
-- 對應 endpoint: GET /api/v1/fundamentals/financial-analysis?stock_id={stk_code}&year={year}
-- 上游資料源：MOPS 財務分析彙整表 t51sb02
SELECT
    stk_code,
    year,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/v1/fundamentals/financial-analysis?stock_id=' || stk_code
            || '&year=' || year::TEXT)::TEXT
    ) AS financial_analysis
FROM {{ schema }}.financial_analysis_year_list
