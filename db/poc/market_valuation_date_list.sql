-- market_valuation_date_list
-- 新增 endpoint 覆蓋：GET /api/market-valuation-summary（全市場本益比/殖利率/股價淨值比彙總）
-- 上游 SQL：無（最上層 `_list`，純日期產生器；本 endpoint 是市場層級彙總，非個股層級，
--   故不依賴 company_basic_info_list）
--
-- 設計理念（rule 11）：此 endpoint 為單日全市場彙總（無 stock_id 維度），沒有回溯 fallback，
--   以「每月一次」取樣作為 PoC 代表性採樣；每月第 5 日通常已避開元旦等長假，提升命中率。
-- 邊界（rule 13：對齊資料源實際覆蓋）：TWSE BWIBBU_d 雖自 2005-09-02 起有資料，但 2017-04-05 以前
--   的 payload 只有 5 欄（代號/名稱/本益比/殖利率/股價淨值比），沒有收盤價；2017-05-05 起才是
--   backend（app/valuation_source.py）解析的 8 欄格式（含收盤價、股利年度、財報年/季）。
--   舊格式無法反推市值，實測 pop 2017-05 以前 139 列全部 NULL，故起點取 2017-05-05。
--   若 backend 日後支援舊格式（例如另抓 MI_INDEX 收盤價），可把起點往回調。
-- 已知限制：5 日落在週末 / 假日時 endpoint 無回溯（2017-05 以後約 1/3 列為 NULL），需 backend 加回溯才能解。
-- 連動：company_valuation_monthly_list 的取樣格點必須與本表相同，改起點時要同步改。
SELECT
    generate_series(
        make_date(2017, 5, 5),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS valuation_date
