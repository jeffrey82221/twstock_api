---
id: I015
title: company_list 含興櫃／已下市代號，兩條股利歷史路徑都對它們發查
severity: S3
layer: L4（seed 範圍）
status: closed
pop_tables: [raw_dividend_history_yfinance, raw_dividend_history]
seeds: [company_list, company_basic_info_list, company_finmind_list]
found_by: [I014 後續]
---

## 症狀（附可重現的 SQL）

`company_list` 來自產業鏈（`chain_info_list`），是產業鏈公司全集，含 187 個興櫃或已下市代號（`raw_company_info` 回 found=false，`company_basic_info_list` 不收）。直接以 `company_list` 為 seed 的 raw 會對這些代號發查：

```sql
SELECT (SELECT count(*) FROM pop.raw_company_info WHERE (basic->>'found')::bool IS NOT TRUE) AS unlisted,
       (SELECT count(*) FROM pop.raw_dividend_history_yfinance r
         WHERE NOT EXISTS (SELECT 1 FROM pop.company_basic_info_list b WHERE b.stk_code = r.stk_code)) AS yf_unlisted,
       (SELECT count(*) FROM pop.raw_dividend_history r
         WHERE NOT EXISTS (SELECT 1 FROM pop.company_basic_info_list b WHERE b.stk_code = r.stk_code)) AS finmind_unlisted
```

修復前：187／187／91。

## 證據（逐條路徑檢查「移除會不會掉資料」）

| 路徑 | seed | 非上市櫃列數 | 其中有資料 | 結論 |
| --- | --- | ---: | ---: | --- |
| `raw_dividend_history_yfinance`（yfinance） | `company_list` | 187 | **0**（154 列 found=false，33 列 found=true 但 `events` 為空） | 純浪費，可移除 |
| `raw_company_info`（TWSE／TPEx OpenAPI + 經濟部） | `company_list` | 187 | 0（basic、business_items 都 found=false） | 這一步就是判定「是否上市櫃」的地方，不能拿掉；`/basic` 查已快取的全市場清單，查不到時 business-items 直接返回，不打上游，成本可忽略 |
| `raw_dividend_history`（FinMind） | `company_finmind_list` ← `company_list` | 91 | **70** | **不是空的**：已下市（2888 新光金、8359 錢櫃、8329 台視、2741 老四川…）與興櫃（7861 貝爾威勒、7869 宏于電機…）都有完整股利歷史 |

下游 seed `dividend_event_list`、`dividend_event_yfinance_list` 裡沒有任何非上市櫃代號（0／0），所以改 yfinance 那條路不會留下孤兒列。

## 根因

L4：`raw_dividend_history_yfinance` 以「產業鏈公司全集」為 seed，但 Yahoo 只收錄上市櫃代號。其他「每家公司打一次 API」的 seed（年報、季報、估值、月營收…）早就從 `company_basic_info_list` 出發，只有這條和 FinMind 這條例外。

不採用「讓 `company_list` 本身只留上市櫃」的做法：那需要一張全市場上市櫃名單的 IMMV，而 IMMV 的 seed 一旦插入就固定、不會重抓，之後新上市的公司會永遠被濾掉。

## 修復

- `db/poc/raw_dividend_history_yfinance.sql`：`FROM company_list` → `FROM company_basic_info_list`。輸出欄位不變，成功列的值不變，只移除必定為空的列。
- `db/poc/README.md`：lineage 同步；順便更正 `raw_dividend_history.stk_code` 的 lineage（實際來源是 `company_finmind_list`）。
- **FinMind（`company_finmind_list`）**：第一輪沒動（移除會丟掉 70 家公司的股利歷史）。使用者決定也只留上市櫃，見「修復（第二輪，FinMind）」。

上線（README §10.5 的 pin 流程，不打上游）：

1. `Pipeline.create_views()`
2. `pop_maintenance.pin_seed_responses(conn, "company_basic_info_list", find_immvs=lambda cur, seed: ["raw_dividend_history_yfinance"])`：pin 870 個既存回應
3. `Pipeline.update_mat_views(["raw_dividend_history_yfinance"])`（recreate_immv，CASCADE 掉的 `hidden.dividend_event_yfinance_list` 由同一呼叫重建）
4. `pop_maintenance.unpin_responses(conn, urls)`

## 重爬與驗證

| 項目 | 前 | 後 |
| --- | ---: | ---: |
| `raw_dividend_history_yfinance` 列數 | 1,057 | 870 |
| 其中有股利事件的列 | 834 | 834 |
| usable% | 85.4 | 100 |
| `dividend_event_yfinance_list` | 460 | 460 |
| `dividend_yfinance` | 460 | 460 |
| http_log 新增 | — | 0 |

`pop_definition_drift` 全部 ok；離線測試 493 passed。

## 修復（第二輪，FinMind；使用者決定只留上市櫃）

取捨：每輪省 91 次 FinMind 呼叫（約 30% 的每小時額度），股利歷史與其他表對齊同一個母體（目前上市櫃公司）；代價是那 70 家非上市櫃公司（含已下市的新光金、錢櫃、台視）的股利歷史從資料庫移除。

- `db/poc/company_finmind_list.sql`：`FROM company_list` → `FROM company_basic_info_list`（欄位不變，rule 18 只換 hidden view）。
- `db/poc/README.md`：lineage 與章節索引（`company_finmind_list` 移到 `company_basic_info_list` 之後）同步。
- `tests/pipeline/test_pop_update.py`：「下游 seed 等上游 seed 建好才能編譯」的測試例子改用 `company_basic_info_list` → `company_finmind_list`。

上線：

1. `Pipeline.create_views()` → `Pipeline.update_mat_views(["company_finmind_list"])`（replace_hidden_view）
2. `pop_maintenance.delete_seed_rows(conn, p, "company_finmind_list", {"stk_code": <91 個代號>})`：先 dry run 確認 91 → 91 一對一；刪除時 pin 住回應（pg_ivm 刪除會重跑 IMMV 查詢），結束後自動清除

| 項目 | 前 | 後 |
| --- | ---: | ---: |
| `company_finmind_list` | 514 | 423 |
| `raw_dividend_history` | 514 | 423 |
| 其中有股利事件的列 | 483 | 413 |
| `dividend_event_list` | 51 | 51 |
| http_log 新增 | — | 0 |

`pop_definition_drift` 全部 ok；`run_checks.py --all` 不變（23 PASS，Q005 C1 刻意 FAIL）；離線 493 passed。

相關的既有限制：其他 seed 本來就只從 `company_basic_info_list`（目前上市櫃）出發，所以已下市公司的估值、財報、月營收原本就不在資料庫裡。若之後要做無倖存者偏差的回測，需要另一份「歷史上市櫃名單」作為 seed，這是比本 issue 更大的範圍。

## 提交與 CI（指令 8）

- 7e2cf86（本 issue）：PR #81 的 8 個 check 全部通過，含 test-live-sql。
- 前一個 commit 36de80e 修的是 test-live-sql 的 prewarm 網址：I002 後還寫死 `as_of=2013-03-31`，API 回 400，所以 prewarm 沒有預熱，冷的 MOPS 下載超過 25 秒回 504。那次 CI 的 test-live-sql 仍失敗，但原因不同：該台 runner 從第一個請求起就被 TWSE／MOPS 回 307 WAF 封鎖頁（`rate_limited=True`）。下一台 runner（7e2cf86）全過。
