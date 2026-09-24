-- market_valuation_summary
-- 上游：raw_market_valuation_summary
-- 欄位 align MarketValuationSummaryResponse + MarketValuationSummary（彙總層級欄位）
-- 主鍵：(valuation_date)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體所有 rows（含 found=false 的非交易日列）。
-- 註：`sample_constituents` 陣列明細未在本 view 攤平（PoC 範圍聚焦市場彙總指標）；
--   如需成分股層級明細，請改用 company_valuation（單股逐日 endpoint）。
SELECT
    valuation_date,
    custom.parse_iso_date(market_valuation_summary->>'date') AS as_of,
    market_valuation_summary->>'market_scope' AS market_scope,
    market_valuation_summary->>'calculation_method' AS calculation_method,
    (market_valuation_summary->'summary'->>'total_market_cap')::NUMERIC AS total_market_cap,
    (market_valuation_summary->'summary'->>'total_market_cap_per_basis')::NUMERIC AS total_market_cap_per_basis,
    (market_valuation_summary->'summary'->>'total_market_cap_pbr_basis')::NUMERIC AS total_market_cap_pbr_basis,
    (market_valuation_summary->'summary'->>'total_market_cap_yield_basis')::NUMERIC AS total_market_cap_yield_basis,
    (market_valuation_summary->'summary'->>'total_net_income')::NUMERIC AS total_net_income,
    (market_valuation_summary->'summary'->>'total_book_value')::NUMERIC AS total_book_value,
    (market_valuation_summary->'summary'->>'total_cash_dividend')::NUMERIC AS total_cash_dividend,
    (market_valuation_summary->'summary'->>'market_per')::NUMERIC AS market_per,
    (market_valuation_summary->'summary'->>'market_pbr')::NUMERIC AS market_pbr,
    (market_valuation_summary->'summary'->>'market_dividend_yield_pct')::NUMERIC AS market_dividend_yield_pct,
    (market_valuation_summary->'summary'->>'total_rows')::NUMERIC AS total_rows,
    (market_valuation_summary->'summary'->>'constituent_count')::NUMERIC AS constituent_count,
    (market_valuation_summary->'summary'->>'per_included')::NUMERIC AS per_included,
    (market_valuation_summary->'summary'->>'pbr_included')::NUMERIC AS pbr_included,
    (market_valuation_summary->'summary'->>'yield_included')::NUMERIC AS yield_included,
    (market_valuation_summary->'summary'->>'excluded_count')::NUMERIC AS excluded_count,
    (market_valuation_summary->'summary'->>'excluded_no_price')::NUMERIC AS excluded_no_price,
    (market_valuation_summary->'summary'->>'excluded_no_shares')::NUMERIC AS excluded_no_shares
FROM {{ schema }}.raw_market_valuation_summary
