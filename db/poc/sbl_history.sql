-- sbl_history
-- 上游：raw_sbl_history
-- 欄位 align SblHistoryResponse + SblHistoryRecord
-- 攤平：每列 = 該次呼叫命中的 31 日視窗內的一筆借券還券紀錄
-- 設計理念（rule 16）：只用 CROSS JOIN LATERAL + COALESCE 保空陣列攤平，符合 pg_ivm 相容性。
-- 設計理念（rule 13）：不做額外 WHERE 過濾；found=false 或 records=[] 的月份自然攤平 0 列。
SELECT
    stk_code,
    custom.parse_iso_date(sbl_history->>'as_of') AS as_of,
    sbl_history->>'stock_id' AS stock_id,
    custom.parse_iso_date(sbl_history->>'data_date') AS data_date,
    custom.parse_iso_date(record->>'transaction_date') AS transaction_date,
    record->>'stock_name' AS stock_name,
    record->>'transaction_type' AS transaction_type,
    (record->>'quantity_lots')::NUMERIC AS quantity_lots,
    (record->>'fee_rate_pct')::NUMERIC AS fee_rate_pct,
    (record->>'completion_close_price')::NUMERIC AS completion_close_price,
    custom.parse_iso_date(record->>'completion_date') AS completion_date,
    (record->>'lending_days')::NUMERIC AS lending_days
FROM {{ schema }}.raw_sbl_history
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(sbl_history->'records', '[]'::jsonb)) AS record
