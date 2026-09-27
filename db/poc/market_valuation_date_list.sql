-- market_valuation_date_list
-- 新增 endpoint 覆蓋：GET /api/market-valuation-summary（全市場本益比/殖利率/股價淨值比彙總）
-- 上游 SQL：無（最上層 `_list`，純日期產生器；本 endpoint 是市場層級彙總，非個股層級，
--   故不依賴 company_basic_info_list）
--
-- 設計理念（rule 11）：此 endpoint 為單日全市場彙總（無 stock_id 維度），沒有回溯 fallback，
--   以「每月一次」取樣作為 PoC 代表性採樣；每月第 5 日通常已避開元旦等長假，提升命中率。
-- 邊界：TWSE BWIBBU_d 資料起始日為 2005-09-02（app/valuation_source.py VALUATION_MIN_DATE）。
SELECT
    generate_series(
        make_date(2005, 10, 5),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS valuation_date
