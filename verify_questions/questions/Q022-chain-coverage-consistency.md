---
id: Q022
title: 半導體產業鏈上中下游各有幾家公司已進資料庫母體？產業鏈表的公司名稱與層級和基本資料一致嗎？
strategy: industry           # §5.1 產業
status: draft
stocks: []                   # 產業鏈層級（ic_code D000），不是個股
window: {since: null, until: 2026-11-27}
pop_tables: [chain_info_list, company_basic_info_list]
crawl: []                    # 只讀；母體覆蓋率是決策本身，不為了提高覆蓋率而補爬（company_list 每列打經濟部，§8.3）
issues: []
last_run: null
---

## 決策情境
產業層級的比較（「上游估值比下游低」「中游成長最快」）要先知道母體夠不夠：某一段在 `company_basic_info_list` 的覆蓋率 < 50% → 該段的產業結論不可信，要先補爬 `company_list`；
≥ 50% → 可以做。這題回答「現在能不能做半導體產業鏈的段別比較」。

## 決策 SQL
```sql
SELECT c.segment_key, count(DISTINCT c.stk_code) AS members, count(DISTINCT b.stk_code) AS in_population,
       round(100.0 * count(DISTINCT b.stk_code) / count(DISTINCT c.stk_code), 1) AS coverage_pct,
       count(DISTINCT b.stk_code) * 2 >= count(DISTINCT c.stk_code) AS enough
FROM pop.chain_info_list c LEFT JOIN pop.company_basic_info_list b ON b.stk_code = c.stk_code
WHERE c.ic_code = 'D000'
GROUP BY c.segment_key ORDER BY c.segment_key
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 母體（`company_basic_info_list`）的每家公司都屬於至少一條產業鏈
全表 872／872（2026-11-27）。母體是 `company_list` → `raw_company_info` 判定上市櫃的公司，產業鏈表涵蓋全部 2,250 家上市櫃公司，母體裡的公司不在產業鏈表就是代號或名單錯。
```sql
SELECT b.stk_code, b.short_name, b.industry_name FROM pop.company_basic_info_list b
WHERE NOT EXISTS (SELECT 1 FROM pop.chain_info_list c WHERE c.stk_code = b.stk_code)
```

### C2 cross-source — 產業鏈表的公司名稱 = 基本資料的公司簡稱（產業價值鏈平台 vs TWSE／TPEx 公司基本資料）
兩個不同上游（`app/icchain.py` 抓的產業價值鏈資訊平台 vs `company_list` 的 OpenAPI），名稱一致才能確定代號沒有對錯公司。全部 872 家 0 不一致（2026-11-27）。
```sql
SELECT DISTINCT c.stk_code, c.company_name AS chain_name, b.short_name AS basic_name
FROM pop.chain_info_list c JOIN pop.company_basic_info_list b ON b.stk_code = c.stk_code
WHERE c.company_name <> b.short_name
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 872 = 母體公司數）：
```sql
SELECT count(DISTINCT c.stk_code) FROM pop.chain_info_list c JOIN pop.company_basic_info_list b ON b.stk_code = c.stk_code
```

### C3 uniqueness — 同一家公司在同一個子鏈只出現一次（同 Q017 C3，全表範圍）
```sql
SELECT ic_code, segment_key, top_code, sub_code, stk_code, count(*) AS n
FROM pop.chain_info_list GROUP BY 1, 2, 3, 4, 5 HAVING count(*) > 1
```

### C4 validity — 代號格式；主鏈代碼與產業鏈同字首、子鏈代碼與主鏈同字首（層級不會錯掛）
全表 6,889 列 0 違規（2026-11-27）。
```sql
SELECT ic_code, top_code, sub_code, stk_code FROM pop.chain_info_list
WHERE stk_code !~ '^[0-9]{4}[0-9A-Z]{0,2}$' OR left(top_code, 1) <> left(ic_code, 1) OR left(sub_code, 1) <> left(top_code, 1)
```

## 已知限制
- `segment_key` 不只有上中下游：其他產業鏈有「應用與服務」「軟體暨工具」「金融」等段別（全表 10 種），C4 不限制段別名稱。
- 產業鏈表本身不在 `company_list` 母體的依賴鏈上（它是 `company_list` 的上游），所以覆蓋率低是「還沒爬到」，不是品質問題；2026-11-27 全表 2,250 家中 872 家在母體（38.8%）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
