-- dividend
-- 上游：raw_dividend
-- 攤平：每列 = 該公司歷史某次除息事件對應的股利明細
-- 欄位 align DividendSection；命名規則同 financial_quarterly。
--
-- 設計理念（rule 15）：每列對應歷史一次真實除息事件（by cash_ex_dividend_date），
--                    非規則性時間格點採樣。
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體所有 rows。
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
FROM {{ schema }}.raw_dividend
