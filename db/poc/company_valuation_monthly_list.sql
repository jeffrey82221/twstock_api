-- company_valuation_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company-valuation（單一上市公司估值展開；反推 EPS/BVPS/DPS）
-- 上游 SQL：company_basic_info_list + market_valuation_date_list
--
-- 設計理念（爬取效率）：本 endpoint 與 /api/market-valuation-summary 共用同一份 TWSE BWIBBU_d
--   payload（backend 依日期磁碟 cache）。直接沿用 market_valuation_date_list 已產生的日期集合
--   （而非各自 generate_series），確保兩條 chain 在同一天觸發同一份上游 payload 的 cache，
--   不會因為取樣日不對齊（例如一個用每月 1 日、另一個用每月 5 日）而讓 TWSE 被打兩次。
-- 邊界：`valuation_date >= listing_date` 排除公司尚未掛牌前的日期，避免無意義的呼叫。
SELECT
    c.stk_code,
    d.valuation_date
FROM {{ schema }}.company_basic_info_list c
CROSS JOIN {{ schema }}.market_valuation_date_list d
WHERE c.listing_date IS NOT NULL
  AND d.valuation_date >= c.listing_date
