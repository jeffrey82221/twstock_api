-- company_finmind_list
-- 上游：company_basic_info_list（上市櫃公司，每家公司一列）
-- 設計理念（rule 20）：company_list 同時是 TWSE OpenAPI（raw_company_info）、yfinance
--   （raw_dividend_history_yfinance）與 FinMind（raw_dividend_history）的 seed。FinMind 免費層
--   300 req/hr，company_list 以 TWSE / yfinance 的速度推進時（實測 36 家/分），FinMind 約 100 次後
--   回 402 "Requests reach the upper limit"，之後每家公司在 raw_dividend_history 都被固定成
--   found=false 的空列（seed 已插入，IMMV 不會再重抓）。拆出這張 FinMind 專用 seed，讓 FinMind
--   端能以自己的限流節奏填充，company_list 則可全速推進。
-- 只取上市櫃公司（I015，使用者決定）：company_list 是產業鏈公司全集，含興櫃／已下市代號
--   （raw_company_info found=false，company_basic_info_list 不收）。資料庫的分析母體是目前上市櫃
--   公司（其他 seed 都從 company_basic_info_list 出發），股利歷史也對齊同一個母體。
-- 只讀 pop.company_basic_info_list（已爬到的上市櫃公司），所以永遠落在 company_list 之後，不會領先。
SELECT
    stk_code,
    company_name
FROM {{ schema }}.company_basic_info_list
