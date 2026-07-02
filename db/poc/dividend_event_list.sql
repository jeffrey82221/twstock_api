-- dividend_event_list
-- 上游：raw_dividend_history（每公司一包歷史 events array）
-- 用途：作為 raw_dividend 的上游母體 — 遍歷該公司歷史所有除息日 (cash_ex_dividend_date)
--
-- 設計理念（rule 15）：
--   股利屬「事件性資料」，事件母體應由資料本身告訴我們（哪些日期真正有除息），
--   而非以規則性時間格點（每年年初、每月月首）採樣。這裡由 raw_dividend_history
--   攤平出 (stk_code, cash_ex_dividend_date) — 每筆對應一次真實的除息事件。
--
-- 設計理念（rule 6 uniqueness）：以 (stk_code, cash_ex_dividend_date) 為唯一 key，
--   同一公司同一除息日不重複；FinMind 若同日有多筆（罕見），僅取其中一筆歷史欄位。
--
-- 註：本檔為 _list 但不呼叫 HTTP（rule 2）— 上游已由 raw_dividend_history 完成抓取。
SELECT 
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (event->>'cash_ex_dividend_date'))) AS cash_ex_dividend_date
FROM {{ schema }}.raw_dividend_history
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(dividend_history->'events', '[]'::jsonb)
) AS event
--WHERE (event->>'cash_ex_dividend_date') IS NOT NULL
