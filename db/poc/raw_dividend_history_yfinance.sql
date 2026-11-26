-- raw_dividend_history_yfinance
-- 上游：company_basic_info_list（上市櫃公司，每家公司抓一次）
-- 對應 endpoint: GET /api/company/{stock_id}/dividend/history/yfinance
-- 上游資料源：yfinance Python Library — Ticker.dividends（整段歷史）
--
-- 與 raw_dividend_history (FinMind 版) 結構完全一致，純資料源替代版。
-- 只取上市櫃公司（I015）：company_list 含興櫃／已下市代號，Yahoo 對它們沒有股利歷史（實測 187 檔全部 0 筆），
--   每次呼叫（含判斷是否合法無資料的股價查詢，I014）都是空的。
SELECT
    stk_code,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code || '/dividend/history/yfinance')::TEXT
    ) AS dividend_history
FROM {{ schema }}.company_basic_info_list
