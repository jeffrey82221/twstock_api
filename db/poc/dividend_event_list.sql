-- dividend_event_list
-- 上游：raw_dividend_history（每公司一包歷史 events array）
-- 用途：作為 raw_dividend 的上游母體 — 遍歷該公司歷史所有股利事件 (reference_date)
--
-- 設計理念（rule 15）：
--   股利屬「事件性資料」，事件母體應由資料本身告訴我們（哪些日期真正有事件），
--   而非以規則性時間格點（每年年初、每月月首）採樣。這裡由 raw_dividend_history
--   攤平出 (stk_code, reference_date) — 每筆對應一次真實的股利事件。
--
-- 設計理念（rule 17）：事件 key 選 reference_date（歷史列皆非 NULL），不選 cash_ex_dividend_date
--   （只有股票股利、無現金股利的年份為 NULL），因此不需要 WHERE IS NOT NULL。
--
-- 設計理念（rule 6 uniqueness）：以 (stk_code, reference_date) 為唯一 key。SQL 本身不去重，
--   依賴上游 events 同公司同 reference_date 不重複（實測 pop 3,709 筆 events 重複 0、NULL 0）。
--
-- 註：本檔為 _list 但不呼叫 HTTP（rule 2）— 上游已由 raw_dividend_history 完成抓取。
SELECT 
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (event->>'reference_date'))) AS reference_date
FROM {{ schema }}.raw_dividend_history
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(dividend_history->'events', '[]'::jsonb)
) AS event
--WHERE (event->>'cash_ex_dividend_date') IS NOT NULL
