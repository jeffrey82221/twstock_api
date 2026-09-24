-- raw_market_valuation_summary
-- 上游：market_valuation_date_list（每月一筆 valuation_date）
-- 對應 endpoint: GET /api/market-valuation-summary?date={valuation_date}&sample_size=5
-- 上游資料源：TWSE 收盤後個股本益比、殖利率及股價淨值比 BWIBBU_d
SELECT
    valuation_date,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/market-valuation-summary?date=' || custom.date_to_iso(valuation_date)::TEXT
            || '&sample_size=5')::TEXT
    ) AS market_valuation_summary
FROM {{ schema }}.market_valuation_date_list
