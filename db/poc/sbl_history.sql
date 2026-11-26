-- sbl_history
-- 上游：raw_sbl_history
-- 欄位 align SblHistoryResponse + SblHistoryRecord
-- 攤平：每列 = 該次呼叫命中的 31 日視窗內、且還券日落在 (as_of − 1 個月, as_of] 的一筆借券還券紀錄
-- 設計理念（rule 16）：只用 CROSS JOIN LATERAL + COALESCE 保空陣列攤平，符合 pg_ivm 相容性；
--   WHERE 只有單純比較，沒有 subquery。
-- 去重（verify_questions I017）：backend 的視窗固定 31 天（[as_of − 30, as_of]），但 seed 每月 5 日取樣、
--   相鄰取樣只差 28–31 天，視窗會重疊 1–3 天，同一筆還券紀錄被兩個 as_of 各存一次（實測 290 組、多 302 列）。
--   只保留還券日晚於「上一個取樣日」（as_of − 1 個月）的紀錄，每筆紀錄恰好歸屬一個 as_of。
--   這是正規化層的資料品質清理，不是隱藏母體邊界（rule 13 管的是 _list）。
--   `date - interval` 是 immutable 運算子（rule 1）。found=false 或 records=[] 的月份仍自然攤平 0 列。
-- as_of 直接沿用 raw_sbl_history.as_of（= 呼叫時帶入的 month_start_date，與回應 JSON 的
--   as_of 相同）而非從 JSON 解析：plain pass-through 欄位可讓 `WHERE as_of >= ...` 下推到
--   sbl_history_monthly_list，只打需要的月份（與 foreign_ownership 同做法）。
SELECT
    stk_code,
    as_of,
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
WHERE custom.parse_iso_date(record->>'completion_date') > as_of - INTERVAL '1 month'
