---
id: I018
title: finmind_news 22% 是重複 link（上游同一份回應就重複），而且每月只收取樣日當天的新聞
severity: S3                 # 目前沒有問題用到新聞；有事件題時重複會讓新聞數多算（S1）
dimension: 唯一性（另含完整性：每月只有 1 天）
layer: L0（上游重複）＋ L3（seed 只取 1 天）
status: diagnosed            # 修復方案待使用者決定
pop_tables: [raw_finmind_news, finmind_news]
seeds: [finmind_news_monthly_list]
found_by: [2026-11-27 指令 1 維度剖析]
---

## 症狀（附可重現的 SQL）

```sql
SELECT stk_code, link, count(*) AS n, count(DISTINCT as_of) AS n_as_of, array_agg(DISTINCT left(title, 20)) AS titles
FROM pop.finmind_news GROUP BY stk_code, link HAVING count(*) > 1 ORDER BY n DESC
```

321 個 link 重複，多 431 列（全表 1,955 列的 22%）。

## 證據

- 重複在 raw 就存在：2330 的 69 列 raw 共 1,955 則，其中只有 1,526 個不同 link。正規化 SQL 只是攤平，沒有製造重複。
- 兩種重複：同一篇「不斷更新」文章收錄多個標題版本（例：ETtoday 2022-07-05 同一 link 6 個標題）；Yahoo股市與奇摩股市轉載同一 link，`source` 不同。
- 跨 as_of 的 1 個 link 是 CMoney「台積電(2330) 個股概覽」，每天都出現，不是新聞。
- `app/finmind_news_source.py` 只送 `start_date=<as_of>`，所以每個 as_of 只拿到當天的新聞：`finmind_news` 只有每月 5 日的新聞，不能拿來做「重大新聞後的股價反應」這類事件研究。
- 實體歸屬是對的：1,955 則標題全部含「台積／2330／TSMC」。

## 根因

L0：FinMind 的新聞資料以「標題版本＋來源」為單位，同一個 link 多列。L3：seed 每月一個取樣日、adapter 只查單日，覆蓋率是設計使然。

## 同類掃描

其他表的實體重複見 I017 的同類掃描與 LEDGER 剖析基線：`company_business_items` 也是上游清單重複（11 家、多 263 列），列為合法。

## 修復方案（待使用者決定）

- **去重**：A（L1）`app/finmind_news_source.py` 依 `link` 去重、保留最新標題，再用 §10.5 的 pin 流程以新 adapter 重建不可行（pin 會重用舊回應），要重爬 69 列 FinMind（分批，§8.3 每題 20）；或 B（L7）不改資料，事件題自己去重並寫進已知限制。
- 不能在正規化 SQL 用 `DISTINCT ON` 去重：db/poc/README.md rule 16 的附註要求 pg_ivm 的 view 避免 `DISTINCT`、window function、`GROUP BY`（見下方規則檢查）。

## db/poc 規則檢查

| 規則 | 檢查 | 結果 |
| --- | --- | --- |
| rule 16 附註（避免 `DISTINCT`、window、`GROUP BY`、CTE） | 原本想在 `finmind_news.sql` 用 `DISTINCT ON (stk_code, link)` | 不符合，改成方案 A（adapter 去重）或 B |
| rule 18 | 方案 A 不改 SQL；回應的欄位不變 | 不需要 `_v2` |
| 覆蓋（seed 改日頻） | rule 11（不過度密集發查）、rule 15（新聞屬事件資料，應有事件母體 endpoint） | 要做的話先新增事件母體 endpoint，不是單純把月格點改成日格點 |
- **覆蓋**：要做事件研究就得改成逐日取樣（seed 改日頻）。FinMind 免費層 300 req/hr、cron 已用約 240，逐日會多出每檔每年約 250 次呼叫，需要先決定要不要做。
- 去重之前，`snapshot.py` 剖析會持續報告這 431 列（剖析基線列為「缺陷 I018」）。
