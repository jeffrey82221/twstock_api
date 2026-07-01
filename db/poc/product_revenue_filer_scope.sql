-- product_revenue_filer_scope
-- 用途：MOPS 各項產品業務營收「歷史掃描範圍」的定義層（時間邊界 + 市場列表）。
--
-- 設計理念（rule 13 + rule 15 子則）：
--   product_revenue 資料很大（每月 × 每市場 × 每公司），PoC 階段預設只掃「近 5 年」，
--   避免第一次載入就吃掉幾百次 MOPS 呼叫。若未來要做完整歷史 (~2006 起)，只需
--   把這裡的 `INTERVAL '5 years'` 改為 `INTERVAL '20 years'`（或直接改為某個常數起始日）。
--
-- 產出：(ym TEXT 民國年月 5 碼, market TEXT sii|otc)
--   ym 已避免民國年 0 前的邊界；MOPS ajax_t05st08_all 最早 2006-01 前後可用。
--
-- 註：本檔為衍生 view（非 _list），不呼叫 HTTP；下游的 raw_product_revenue_filers 才對每一列打 API。
SELECT
    LPAD((EXTRACT(YEAR FROM month_date)::INT - 1911)::TEXT, 3, '0')
        || LPAD(EXTRACT(MONTH FROM month_date)::INT::TEXT, 2, '0') AS ym,
    market
FROM (
    SELECT generate_series(
        make_date(EXTRACT(YEAR FROM (CURRENT_DATE - INTERVAL '5 years'))::INT,
                  EXTRACT(MONTH FROM (CURRENT_DATE - INTERVAL '5 years'))::INT,
                  1),
        make_date(EXTRACT(YEAR FROM CURRENT_DATE)::INT,
                  EXTRACT(MONTH FROM CURRENT_DATE)::INT,
                  1),
        INTERVAL '1 month'
    )::DATE AS month_date
) months
CROSS JOIN (VALUES ('sii'), ('otc')) AS m(market)
