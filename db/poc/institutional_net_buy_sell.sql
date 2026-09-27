-- institutional_net_buy_sell
-- 上游：raw_institutional_net_buy_sell
-- 欄位 align InstitutionalNetBuySellResponse + InstitutionalNetBuySellRow
-- 主鍵：(stk_code, as_of)
-- 設計理念（rule 13）：不做 WHERE 過濾，保留 raw 母體的所有 rows（含 row=null 的非交易日列）。
SELECT
    stk_code,
    custom.parse_iso_date(institutional_net_buy_sell->>'trade_date') AS as_of,
    institutional_net_buy_sell->>'market' AS market,
    custom.parse_iso_date(institutional_net_buy_sell->'row'->>'trade_date') AS trade_date,
    institutional_net_buy_sell->'row'->>'stock_name' AS stock_name,
    (institutional_net_buy_sell->'row'->>'foreign_investors_net_buy_sell')::NUMERIC AS foreign_investors_net_buy_sell,
    (institutional_net_buy_sell->'row'->>'foreign_dealers_net_buy_sell')::NUMERIC AS foreign_dealers_net_buy_sell,
    (institutional_net_buy_sell->'row'->>'investment_trust_net_buy_sell')::NUMERIC AS investment_trust_net_buy_sell,
    (institutional_net_buy_sell->'row'->>'dealers_net_buy_sell')::NUMERIC AS dealers_net_buy_sell,
    (institutional_net_buy_sell->'row'->>'dealers_proprietary_net_buy_sell')::NUMERIC AS dealers_proprietary_net_buy_sell,
    (institutional_net_buy_sell->'row'->>'dealers_hedge_net_buy_sell')::NUMERIC AS dealers_hedge_net_buy_sell,
    (institutional_net_buy_sell->'row'->>'total_institutional_net_buy_sell')::NUMERIC AS total_institutional_net_buy_sell
FROM {{ schema }}.raw_institutional_net_buy_sell
