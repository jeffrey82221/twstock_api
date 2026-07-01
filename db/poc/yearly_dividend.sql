-- yearly_dividend
-- 上游：raw_yearly_dividend
-- 攤平：每行為某公司在某 as_of 命中的「除息日 ≤ as_of」最後一次股利
-- 欄位 align DividendSection；命名規則同 financial_quarterly（`as_of`, `stock_id`, `stk_code`）
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows。
SELECT
    stk_code,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'as_of')::TEXT)) AS as_of,
    TRIM('"' FROM (dividend->'stock_id')::TEXT) AS stock_id,
    TRIM('"' FROM (dividend->'dividend'->>'year')) AS dividend_year,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'reference_date'))) AS reference_date,
    (dividend->'dividend'->>'cash_dividend')::NUMERIC AS cash_dividend,
    (dividend->'dividend'->>'stock_dividend')::NUMERIC AS stock_dividend,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'cash_ex_dividend_date'))) AS cash_ex_dividend_date,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'cash_payment_date'))) AS cash_payment_date,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'stock_ex_dividend_date'))) AS stock_ex_dividend_date,
    custom.parse_iso_date(TRIM('"' FROM (dividend->'dividend'->>'announcement_date'))) AS announcement_date
FROM {{ schema }}.raw_yearly_dividend
