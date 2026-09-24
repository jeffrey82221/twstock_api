-- raw_sbl_history
-- 上游：sbl_history_monthly_list（每公司每月一筆 month_start_date）
-- 對應 endpoint: GET /api/company/{stock_id}/sbl-history?as_of={month_start_date}
-- 上游資料源：TWSE 借券歷史還券明細 SBL t13sa870
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code
            || '/sbl-history?as_of=' || custom.date_to_iso(month_start_date)::TEXT)::TEXT
    ) AS sbl_history,
    month_start_date AS as_of
FROM {{ schema }}.sbl_history_monthly_list
