-- raw_product_revenue
-- 上游：product_revenue_filer_list（每 (stk_code, report_month) 一筆 — 真正有申報的事件）
-- 對應 endpoint: GET /api/company/{stock_id}/product-revenue?as_of={report_month 月末}
-- 上游資料源：公開資訊觀測站 (MOPS) t05st08「各項產品業務營收統計表」
--
-- 設計理念（rule 15）：
--   MOPS 產品營收表 IFRS 後改自願申報，非每公司每月都有。若對 (公司 × 每月) 笛卡兒積打
--   as_of endpoint，會落到 last_filed_ym 產生大量重複、也浪費 API。改由事件母體
--   product_revenue_filer_list 每列（該公司實際申報過的月份）觸發一次呼叫。
--
-- as_of 選當月月首（report_month 本身）— get_product_revenue_at 的回溯邏輯會鎖定到該月申報明細。
--   * 用月首而非月末，是因為 make_date 是 IMMUTABLE 而 INTERVAL 位移轉 DATE 需要 STABLE cast。
--   * MOPS 各項產品業務營收「月」的 filing 對 as_of 落在該月任一日皆會鎖定同一次申報。
SELECT
    stk_code,
    ym,
    report_month,
    custom.http_get_content(
        ('http://host.docker.internal:5002/api/company/' || stk_code
            || '/product-revenue?as_of=' || custom.date_to_iso(report_month)::TEXT)::TEXT
    ) AS product_revenue
FROM {{ schema }}.product_revenue_filer_list
