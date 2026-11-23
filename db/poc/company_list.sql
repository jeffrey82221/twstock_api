-- 公司清單（跨產業鏈去重）。
-- 上游：chain_info_list（rule 8 / rule 21：raw_chain_info 的 3 層 lateral 展開已物化成
--       pop.chain_info_list，本表只讀它，不再自己呼叫 /api/chain/*；rule 2：_list 不呼叫
--       http_get_content）。
-- 去重：以 stk_code 為唯一 key（rule 6 / rule 7）。同一公司在不同產業鏈的顯示名稱若不一致，
--       DISTINCT stk_code, company_name 會產生同 stk_code 兩列、讓下游 raw_* 重複發查，
--       因此用 GROUP BY stk_code + MIN(company_name)。本表是 seed（pop 內為一般 table，
--       hidden 內為一般 view），不經 pg_ivm 展開，GROUP BY 不受 rule 16 限制。
SELECT
	stk_code,
	MIN(company_name) AS company_name
FROM {{ schema }}.chain_info_list
GROUP BY stk_code
