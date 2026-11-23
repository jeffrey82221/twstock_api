-- company_valuation_monthly_list
-- 新增 endpoint 覆蓋：GET /api/company-valuation（單一上市公司估值展開；反推 EPS/BVPS/DPS）
-- 上游 SQL：company_basic_info_list
--
-- 設計理念（爬取效率）：本 endpoint 與 /api/market-valuation-summary 共用同一份 TWSE BWIBBU_d
--   payload（backend 依日期磁碟 cache）。取樣日格點必須與 market_valuation_date_list 完全一致
--   （同起點 2017-05-05、每月 5 日；起點理由見 market_valuation_date_list），確保兩條 chain 在同一天觸發同一份上游 payload 的 cache，
--   不會因為取樣日不對齊而讓 TWSE 被打兩次。若調整 market_valuation_date_list 的起點，這裡要同步改。
-- 設計理念（rule 13）：掛牌日邊界直接寫進 generate_series 起點 —— 取「掛牌日當月 5 日；若掛牌日
--   晚於 5 日則下個月 5 日」與 2017-05-05 的較大者，不再「CROSS JOIN 全部日期再用 WHERE 篩掉
--   掛牌前日期」（改寫時以同起點驗證：輸出與舊版相同，85,555 列，雙向 EXCEPT ALL 皆 0）。
-- rule 13 例外：`listing_date IS NOT NULL` 為技術性 guard（無掛牌日的公司不產生取樣日）。
SELECT
    stk_code,
    generate_series(
        GREATEST(
            DATE '2017-05-05',
            make_date(EXTRACT(YEAR FROM listing_date)::INT, EXTRACT(MONTH FROM listing_date)::INT, 5)
                + CASE WHEN EXTRACT(DAY FROM listing_date) > 5 THEN INTERVAL '1 month' ELSE INTERVAL '0 month' END
        ),
        CURRENT_DATE,
        INTERVAL '1 month'
    )::DATE AS valuation_date
FROM {{ schema }}.company_basic_info_list
WHERE listing_date IS NOT NULL
