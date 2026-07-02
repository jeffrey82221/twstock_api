-- raw_product_revenue_filers
-- 上游：product_revenue_filer_scope（時間邊界 × 市場，預設近 5 年）
-- 對應 endpoint: GET /api/product-revenue/filers?ym={ym}&market={market}
-- 上游資料源：MOPS ajax_t05st08_all（該月該市場所有申報「各項產品業務營收」的公司清單）
--
-- 設計理念（rule 15）：
--   MOPS 各項產品業務營收 IFRS 後改自願申報，不是每公司每月都有申報。本 raw 對每
--   (ym, market) 打一次 filers endpoint，取得「真正有申報的 co_id 陣列」，作為
--   下游 product_revenue_filer_list 攤平出事件母體 (co_id × ym) 的來源。
--
-- 每列一次 API 呼叫；預設 5 年 × 12 月 × 2 市場 = 120 次呼叫（MOPS 內部有 24h cache）。
-- 若要擴大到完整歷史，只需調整 product_revenue_filer_scope 的時間邊界。
SELECT
    ym,
    market,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/product-revenue/filers?ym=' || ym || '&market=' || market)::TEXT
    ) AS filers
FROM {{ schema }}.product_revenue_filer_scope_list
