-- raw_mops_quarterly_financials
-- 上游：mops_quarterly_financials_quarter_list（每公司每季末日一筆）
-- 對應 endpoint: GET /api/company/{stock_id}/quarterly-financials?as_of={quarter_end}
-- 上游資料源：MOPS IFRS 季度財務資料（t163sb06 營益分析彙總 + t163sb04 綜合損益表）
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code
            || '/quarterly-financials?as_of=' || custom.date_to_iso(quarter_end)::TEXT)::TEXT
    ) AS quarterly_financials,
    quarter_end AS as_of
FROM {{ schema }}.mops_quarterly_financials_quarter_list
