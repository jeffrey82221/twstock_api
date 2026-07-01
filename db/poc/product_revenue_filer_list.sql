-- product_revenue_filer_list
-- 上游：raw_product_revenue_filers（每 (ym, market) 一包 co_ids 陣列）
-- 用途：作為 raw_product_revenue 的上游母體 — 遍歷「真正有申報的 (co_id, report_month)」。
--
-- 設計理念（rule 15）：
--   product_revenue 是「事件性資料」— 每公司只在自己申報的月份才有明細，非每月都有。
--   事件母體應由資料本身（MOPS 該月申報清單）決定，非以規則性格點採樣。
--   本表把每 (ym, market) 的 co_ids array 攤平成 (co_id, ym, report_month DATE)，
--   每列 = 該公司該月確定有申報的一次事件。
--
-- 設計理念（rule 12）：民國年月字串 ym（例 '11312'）→ 西元 DATE report_month（2024-12-01）。
-- 設計理念（rule 6）：以 (stk_code, report_month) 為唯一 key（同一公司同月僅一次申報）。
SELECT DISTINCT
    co_id AS stk_code,
    ym,
    make_date(
        SUBSTRING(ym, 1, 3)::INT + 1911,
        SUBSTRING(ym, 4, 2)::INT,
        1
    ) AS report_month
FROM {{ schema }}.raw_product_revenue_filers
CROSS JOIN LATERAL jsonb_array_elements_text(
    COALESCE(filers->'co_ids', '[]'::jsonb)
) AS co_id
