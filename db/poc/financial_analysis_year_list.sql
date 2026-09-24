-- financial_analysis_year_list
-- 新增 endpoint 覆蓋：GET /api/v1/fundamentals/financial-analysis（MOPS t51sb02 財務分析彙整表）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（rule 14）：年頻資料使用整數西元年（endpoint 的 `year` 參數即為 INT，不是日期），
--   與既有 financial_year_list（`year_start_date` DATE，供 FinMind/yfinance 用）分開，
--   因為 rule 20：不同資料源（MOPS t51sb02 vs FinMind/yfinance）分流各自 seed。
-- 邊界：t51sb02 資料起始年為民國 101 / 西元 2012（app/mops_financial_analysis.py FA_MIN_YEAR）。
SELECT
    stk_code,
    generate_series(
        GREATEST(EXTRACT(YEAR FROM incorporation_date)::INT, 2012),
        EXTRACT(YEAR FROM CURRENT_DATE)::INT
    ) AS year
FROM {{ schema }}.company_basic_info_list
