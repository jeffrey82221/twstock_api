-- raw_dividend_history
-- 上游：company_list（每家公司抓一次）
-- 對應 endpoint: GET /api/company/{stock_id}/dividend/history
-- 上游資料源：FinMind v4 TaiwanStockDividend（20 年區間全量）
--
-- 設計理念（rule 15）：
--   股利是「事件性資料」（每公司每年 0~數次除息事件），對 (公司 × 每年年初) 打
--   /dividend?as_of=X 只會回 as_of 前最後一次的單筆，多年查詢會產生大量重複。
--   本 raw 每公司一次撈整包歷史 events array，作為下游 dividend_event_list 的來源。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/dividend/history')::TEXT
    ) AS dividend_history
FROM {{ schema }}.company_list
