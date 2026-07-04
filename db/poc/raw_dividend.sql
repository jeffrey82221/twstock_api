-- raw_dividend
-- 上游：dividend_event_list（每公司每個真實除息日一筆）
-- 對應 endpoint: GET /api/company/{stock_id}/dividend?as_of={cash_ex_dividend_date}
-- 上游資料源：FinMind v4 TaiwanStockDividend（透過 _pick_dividend 取 as_of 前最後一筆）
--
-- 設計理念（rule 15）：
--   以「該公司歷史真正的除息日」作為 as_of 遍歷母體。這裡對每筆 (stk_code, ex_date)
--   打一次 /dividend?as_of=ex_date，每次命中的就是「該次除息事件對應的股利明細」。
--   不會落到重複的 last_dividend，也不會浪費 API 打沒事件的日期。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/dividend?as_of=' || custom.date_to_iso(reference_date)::TEXT)::TEXT
    ) AS dividend,
    reference_date AS as_of
FROM {{ schema }}.dividend_event_list
