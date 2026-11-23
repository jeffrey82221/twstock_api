-- financial_quarter_yfinance_list
-- 上游：raw_yearly_financials_yfinance（yfinance Ticker.quarterly_financials 的 TTM 季度清單）
-- 事件母體（rule 15）：每家公司實際揭露過的季度 = eps.ttm_quarters ∪ net_income.ttm_quarters。
-- 唯一性（rule 6）：eps 與 net_income 的季度清單大多重疊（實測 16 列中 4 列重複，全部發生在
--   同一列 raw 內），因此在 LATERAL 子查詢內逐列 DISTINCT。DISTINCT 只作用在單列 raw 展開的
--   幾個元素上，不會像外層 DISTINCT 那樣逼 poc 的 LIMIT 1 先展開全部「公司 × 年度」（每列一次
--   yfinance 呼叫）才吐第一列。
--   殘餘限制：同公司不同年度列若帶回相同季度，仍可能跨列重複（目前資料未出現，因 yfinance 只有
--   最近 1 個年度列有資料）；這種跨列重複無法在不阻斷串流的前提下消除，由 seed insert 的 EXCEPT 去重。
-- WHERE（rule 13 例外）：只濾掉 NULL 季度（ttm_quarters 內含 null 元素時展開出的列），
--   這種列對下游 as_of 呼叫無意義；取代舊版 `quarter <> '[null]'`，舊版擋不住
--   [null, "2025-06-30"] 這類混合陣列。
-- 欄位名 `quater` 為既有 seed 欄位（rule 18：改名會讓 pop 表重建），保留不改。
SELECT
	f.stk_code,
	custom.parse_iso_date(q.value) AS quater
FROM {{ schema }}.raw_yearly_financials_yfinance f
CROSS JOIN LATERAL (
	SELECT DISTINCT e.value
	FROM jsonb_array_elements_text(
		COALESCE((f.financials->'eps')->'ttm_quarters', '[]'::jsonb)
		|| COALESCE((f.financials->'net_income')->'ttm_quarters', '[]'::jsonb)
	) AS e(value)
	WHERE e.value IS NOT NULL
) AS q(value)
