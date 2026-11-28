---
id: Q036
title: 2330 近 3 個月的新聞熱度是否比之前一年明顯升高？新聞資料的連結、標題、日期有效嗎？
strategy: event              # §5.1 事件
status: passing
stocks: ["2330"]
window: {since: 2025-08-05, until: 2026-11-27}
pop_tables: [finmind_news, raw_finmind_news]
crawl: []                    # 出題時（2026-11-28）2330 2021-02 ～ 2026-11 每月都已在 pop
issues: []
last_run: 2026-11-28
---

## 決策情境
最近 3 個取樣日的平均新聞數（以 link 去重）> 之前 12 個取樣日平均的 1.5 倍 → 「新聞熱度升高」，搭配 [Q011](Q011-technical-ma60-volatility.md) 看股價是否已反應；< 0.5 倍 → 「冷門」。
`finmind_news` 每個取樣月只收取樣日當天、同一 link 會重複（[I018](../issues/I018-finmind-news-duplicates-single-day.md)），所以決策以 link 去重、只做相對比較，不解讀成「整個月的新聞數」。

## 決策 SQL
```sql
WITH m AS (
  SELECT as_of, count(DISTINCT link) AS n_news,
         row_number() OVER (ORDER BY as_of DESC) AS rn
  FROM pop.finmind_news WHERE stk_code = '2330' AND as_of >= DATE '2025-08-05'
  GROUP BY as_of
)
SELECT round(avg(n_news) FILTER (WHERE rn <= 3), 1) AS avg_last3,
       round(avg(n_news) FILTER (WHERE rn BETWEEN 4 AND 15), 1) AS avg_prev12,
       round(avg(n_news) FILTER (WHERE rn <= 3) / NULLIF(avg(n_news) FILTER (WHERE rn BETWEEN 4 AND 15), 0), 2) AS ratio,
       count(*) AS n_samples
FROM m
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 2025-08 起每月 5 日都有 raw 列，而且有至少一則新聞（2330 是新聞量最大的股票，取樣日沒有新聞不合理）
```sql
SELECT g::date AS as_of
FROM generate_series(DATE '2025-08-05', CURRENT_DATE, INTERVAL '1 month') AS g
WHERE NOT EXISTS (SELECT 1 FROM pop.finmind_news n WHERE n.stk_code = '2330' AND n.as_of = g::date)
```

### C2 validity — 連結是 http(s) URL、標題與來源非空
全表 1,955 列 0 違規（2026-11-28）。
```sql
SELECT as_of, news_date, title, source, link FROM pop.finmind_news
WHERE stk_code = '2330' AND (link !~ '^https?://' OR coalesce(btrim(title), '') = '' OR coalesce(btrim(source), '') = '')
```

### C3 timeliness — 新聞日落在取樣日或前一天（seed 只抓取樣日當天，[I018](../issues/I018-finmind-news-duplicates-single-day.md)；早於前一天或晚於取樣日代表日期錯置）
全表 0 違規（2026-11-28）。
```sql
SELECT as_of, news_date, title FROM pop.finmind_news
WHERE stk_code = '2330' AND (news_date > as_of OR news_date < as_of - 1)
```

### C4 availability — 窗口內的 raw 列都可用
```sql
SELECT stk_code, as_of FROM pop.raw_finmind_news
WHERE stk_code = '2330' AND as_of >= DATE '2025-08-05'
  AND (finmind_news IS NULL OR finmind_news->>'found' = 'false' OR finmind_news ? 'source_errors')
```

## 已知限制
- **沒有唯一性斷言**：同一 link 重複是已知缺陷 [I018](../issues/I018-finmind-news-duplicates-single-day.md)（每月只收取樣日、同一 link 多個標題版本），決策用 `count(DISTINCT link)` 避開。[I018](../issues/I018-finmind-news-duplicates-single-day.md) 修好後補一個 `uniqueness` 斷言。
- 新聞數只代表取樣日當天，不是整月；比值只適合看相對變化。
- 沒有錯值防線：新聞沒有第二個來源可比（候選：公司重大訊息 MOPS t05st01）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-28 | passing（4／4） | [run](../runs/2026-11-28_Q036.md) | 不需補爬；新聞熱度 1.14 倍，無訊號 |
