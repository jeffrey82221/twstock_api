-- monthly_revenue_twse
-- 上游：raw_monthly_revenue_twse
-- 欄位完全與 monthly_revenue (FinMind 版) align，差別只在資料源 (TWSE/MOPS vs FinMind)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows。
-- as_of 直接沿用 raw_monthly_revenue_twse.as_of（= 呼叫時帶入的 as_of_date，與回應 JSON 的
--   as_of 相同）而非從 JSON 解析：plain pass-through 欄位可讓 `WHERE as_of >= ...` 下推到
--   financial_month_twse_list，只打需要的月份（與 sbl_history 同做法）。
-- Point-in-time（I002）：data_date = 營收月份月底、available_date = 法定公告期限（次月 10 日），
--   endpoint 保證 available_date <= as_of；回測以 as_of（或 available_date）為決策日即可。
SELECT
    stk_code,
    as_of,
    TRIM('"' FROM (revenue->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (revenue->>'latest_month_label')) AS latest_month_label,
    custom.parse_iso_date(revenue->>'data_date') AS data_date,
    custom.parse_iso_date(revenue->>'available_date') AS available_date,
    (revenue->>'latest_month_value')::NUMERIC AS latest_month_value,
    (revenue->>'latest_month_yoy_pct')::NUMERIC AS latest_month_yoy_pct,
    (revenue->>'ttm_value')::NUMERIC AS ttm_value,
    (revenue->>'ttm_yoy_pct')::NUMERIC AS ttm_yoy_pct
FROM {{ schema }}.raw_monthly_revenue_twse
