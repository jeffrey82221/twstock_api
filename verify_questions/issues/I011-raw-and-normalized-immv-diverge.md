---
id: I011
title: 上游中途失敗時，raw IMMV 與正規化 IMMV 各打一次 API，結果可能不一致且無法安全重爬
severity: S3
layer: L6（pop 物化架構）
status: closed
pop_tables: [raw_institutional_net_buy_sell, institutional_net_buy_sell, raw_dividend, dividend]
seeds: [institutional_monthly_list, dividend_event_list]
found_by: [2026-11-25 資料庫重建後的 retry_failed.py]
---

## 症狀（附可重現的 SQL）

重建後還原 `institutional_monthly_list` 時 TWSE 中途封鎖（307）。有 9 列 raw 是 NULL（當下 API 回 502），對應的正規化列卻有值：

```sql
SELECT r.stk_code, r.as_of,
       (SELECT count(*) FROM pop.institutional_net_buy_sell n WHERE n.stk_code = r.stk_code AND n.as_of = r.as_of) AS norm_rows
FROM pop.raw_institutional_net_buy_sell r
WHERE r.institutional_net_buy_sell IS NULL
```

例：1233 2014-04-05 ～ 2014-10-05（7 列）、1220 2012-05-05、1233 2013-05-05：raw NULL、`institutional_net_buy_sell` 有 1 列。

FinMind 也發生（2026-11-26 `retry_failed.py raw_dividend`）：19 列 `raw_dividend` 帶 `source_errors`（限流），正規化 `dividend` 卻有值（2330 的 16 個除息日、2317 1 個、2882 2 個），刪除同樣被護欄 rollback。

## 證據

- `pipeline._get_matview_select_sqls` 把非 seed 上游內嵌進每個 IMMV（見 db/poc/README「IMMV 重複呼叫已修正」）：`raw_institutional_net_buy_sell` 與 `institutional_net_buy_sell` 都直接建在 seed 上，**各自**呼叫 `custom.http_get_content`。
- 同一次 insert 內，raw 的呼叫拿到 502（不快取）→ NULL；正規化的呼叫稍後成功 → 有值。
- `retry_failed.py`（`delete_seed_rows`）對這 9 列 rollback：pin 只能用 raw 存的回應（NULL → 404），pg_ivm 重算正規化列時得到空結果，與存著的列不符 → 會變孤兒，所以拒絕刪除。護欄運作正確。

## 根因

pop 的物化設計讓同一個 seed 列的多個 IMMV 各自取得回應，沒有「同一列只取一次」的保證。上游穩定時沒差；上游在 insert 期間時好時壞就會分歧。L6。

## 修復方案

1. 影響：決策 SQL 讀正規化表（有資料），只有 raw 層的可用率與 `retry_failed.py` 受影響。目前法人 9 列、FinMind 股利 19 列，S3。
2. 選項 A：`delete_seed_rows` 的 pin 改成「以正規化 IMMV 反推回應」——不可行（正規化是有損轉換）。
3. 選項 B：分歧列用 `refresh_immv` 只重建該 raw IMMV（會對該 seed 全部列重打 API，institutional 530 列，可接受但要排在上游低峰）。
4. 選項 C：讓 `custom.http_get_content` 在同一交易內對同一 URL 只打一次（transaction-local memo 已存在，但只記成功回應；改成也記失敗回應）——要確認 5xx 不跨交易快取的語意不變。

## 修復紀錄

- 採選項 B（資料修復）：依序 `pgivm.refresh_immv` raw 與正規化 IMMV。第二次 refresh 全部命中 `job_control.http_cache`，所以兩表必定一致。流程寫進 README §10.5。
- 選項 C（同交易內記住失敗回應，從架構上避免分歧）未做：目前分歧只在上游時好時壞時出現，有 §10.5 的修復流程即可。若之後頻繁發生再做。

## 重爬與驗證

2026-11-26（上游已恢復，先 `curl` 確認）：

- `raw_dividend` → `dividend`（51 列，12 秒＋0 秒，同時套用 [I012](I012-finmind-dividend-window-drops-event.md) 的修正）：raw 可用 33／51 → 51／51。
- `raw_institutional_net_buy_sell` → `institutional_net_buy_sell`（533 列，12 秒＋0 秒，TWSE 日檔多數命中磁碟快取）：raw 失敗 11 → 0，正規化 533 列。
- 之後 [Q001](../questions/Q001-valuation-per-percentile.md)–[Q004](../questions/Q004-flow-foreign-institutional.md) 全 PASS；[Q005](../questions/Q005-income-dividend-consistency.md) 只剩 C1（[I003](I003-yfinance-dividend-adjusted.md)，刻意保留）。
