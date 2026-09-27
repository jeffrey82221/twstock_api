-- finmind_news
-- 上游：raw_finmind_news
-- 欄位 align FinMindNewsResponse + FinMindNewsItem
-- 攤平：每列 = 該 as_of 當日的一則新聞
-- 設計理念（rule 16）：只用 CROSS JOIN LATERAL + COALESCE 保空陣列攤平，符合 pg_ivm 相容性。
-- 設計理念（rule 13）：不做額外 WHERE 過濾；found=false 或 items=[] 的月份自然攤平 0 列
--   （因 404 時 custom.http_get_content 回 NULL，COALESCE 確保仍安全攤平 0 列）。
SELECT
    stk_code,
    custom.parse_iso_date(finmind_news->>'as_of') AS as_of,
    finmind_news->>'stock_id' AS stock_id,
    custom.parse_iso_date(finmind_news->>'data_date') AS data_date,
    custom.parse_iso_date(item->>'date') AS news_date,
    item->>'title' AS title,
    item->>'source' AS source,
    item->>'link' AS link
FROM {{ schema }}.raw_finmind_news
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(finmind_news->'items', '[]'::jsonb)) AS item
