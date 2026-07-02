-- raw_quarterly_financials_yfinance
-- 上游：financial_quarter_yfinance_list（每家公司每個真實有揭露的季底 as_of）
-- 對應 endpoint: GET /api/company/{stock_id}/financials/yfinance?as_of={quarter}
-- 上游資料源：yfinance Python Library (Yahoo Finance)
--
-- 設計理念（rule 1, 9）：本 raw 只負責發 HTTP 並落地 JSON；
-- 型別轉換與欄位攤平留給下游 view `financial_quarterly_yfinance`。
-- 這樣做的目的：把 HTTP 呼叫從 view 內 CROSS JOIN 拆出來，
-- 避免一個 SELECT 觸發數百次同步 HTTP 造成 statement_timeout。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/financials/yfinance?as_of=' || custom.date_to_iso(quater)::TEXT)::TEXT
    ) AS financials,
    quater AS as_of
FROM {{ schema }}.financial_quarter_yfinance_list
