---
id: I015
title: company_list 含興櫃／已下市代號，yfinance 股利歷史對它們每次都是空呼叫
severity: S3
layer: L4（seed 範圍）
status: closed（FinMind 部分待使用者決定）
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
- **FinMind（`company_finmind_list`）不動**：移除會丟掉 70 家公司的股利歷史，其中包括已下市公司，回測時會造成倖存者偏差。待使用者決定（見「待決定」）。

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

## 待決定（FinMind）

`company_finmind_list` 要不要也改成只取上市櫃？

- 改：每輪省 91 次 FinMind 呼叫（約 30% 的每小時額度），但失去 70 家公司的股利歷史（含已下市公司）。
- 不改（建議）：保留已下市公司的歷史，避免倖存者偏差。

相關的既有限制：其他 seed 本來就只從 `company_basic_info_list`（目前上市櫃）出發，所以已下市公司的估值、財報、月營收原本就不在資料庫裡。若之後要做無倖存者偏差的回測，需要另一份「歷史上市櫃名單」作為 seed，這是比本 issue 更大的範圍。
