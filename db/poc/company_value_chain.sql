-- company_value_chain
-- 上游：chain_info（純衍生 view，不額外打 /value-chain endpoint）
-- 設計理念：/company/{id}/value-chain endpoint 與 /chain/{ic_code} 共用同一份 chain_tree raw data，
--           company_index 只是 chain_tree 的反向索引（純 Python dict 操作、無額外 HTTP）。
--           因此公司視角的價值鏈完全可從 chain_info 衍生，符合 PoC schema 規則 #2「同源 endpoint 不重複攤平」。
-- 攤平：每行 = 公司在某條鏈、某段位、某子分類下的一個 membership（與 chain_info 相同顆粒度）
-- 欄位刻意與 chain_info align：ic_code / ic_name / segment_key / top_code / top_name / sub_code / sub_name / stk_code
-- 額外提供 neighbor_stk_code / neighbor_company_name：同 sub_chain 下的鄰居公司（self-join）
SELECT
    me.stk_code,
    me.ic_code,
    me.ic_name,
    me.segment_key,
    me.top_code,
    me.top_name,
    me.sub_code,
    me.sub_name,
    nb.stk_code     AS neighbor_stk_code,
    nb.company_name AS neighbor_company_name
FROM {{ schema }}.chain_info me
LEFT JOIN {{ schema }}.chain_info nb
    ON  nb.ic_code   = me.ic_code
    AND nb.top_code  = me.top_code
    AND nb.sub_code  = me.sub_code
    AND nb.stk_code <> me.stk_code;
