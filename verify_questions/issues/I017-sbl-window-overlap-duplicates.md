---
id: I017
title: 借券相鄰兩次取樣的 31 日視窗重疊，同一筆還券紀錄被兩個 as_of 各存一次
severity: S2                 # 預設 S1（重複計入決策），實測 Q004 只偏 0.3–2.6%、結論不變，降為 S2
dimension: 唯一性
layer: L1（adapter 視窗）／L5 可修
status: diagnosed
pop_tables: [raw_sbl_history, sbl_history]
seeds: [sbl_history_monthly_list]
found_by: [2026-11-27 指令 1 維度剖析, Q009]
---

## 症狀（附可重現的 SQL）

同一筆還券紀錄（股票、借券日、類型、張數、費率、還券日、天數都相同）出現在兩個 as_of：290 組、多 302 列。

```sql
SELECT stk_code, transaction_date, transaction_type, quantity_lots, completion_date,
       array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.sbl_history
GROUP BY stk_code, transaction_date, transaction_type, quantity_lots, fee_rate_pct, completion_date, lending_days
HAVING count(DISTINCT as_of) > 1
```

例：2317 2025-03-27 議借 432 張、還券日 2025-09-05，同時出現在 as_of 2025-09-05 與 2025-10-05。

## 證據

- `app/twse_sbl_source.get_sbl_history`：視窗是 `[as_of − 30, as_of]`（`MAX_LOOKBACK_DAYS = WINDOW_DAYS = 31`）。註解寫「相鄰兩次呼叫的 31 日視窗首尾相接」，但 seed 是每月 5 日，相鄰取樣只差 28–31 天：
  - as_of 2025-10-05 的視窗從 2025-09-05 開始，與上一次（as_of 2025-09-05）重疊 1 天。
  - 3 月 5 日的視窗從 2 月 3 日開始，重疊 3 天。
  - 只有上一個月是 31 天時才不重疊。
- 重複的列全部落在重疊日：在正規化表加上 `completion_date > as_of − INTERVAL '1 month'` 後，27,058 → 26,762 列，跨 as_of 重複 0 組。
- 對決策的影響（Q004 決策 SQL 的借券張數合計）：

  | 股票 | 近 6 個月（原／去重） | 之前（原／去重） |
  | --- | ---: | ---: |
  | 2317 | 573,988／573,988 | 1,080,614／1,073,482 |
  | 2330 | 533,430／533,430 | 1,497,320／1,492,435 |
  | 6488 | 69,718／69,718 | 73,802／71,918 |

  「之前」多算 0.3–2.6%，「近 6 個月 vs 之前」的方向不變。
- 同一 as_of 內另有 445 列條件完全相同的紀錄（325 組）。上游沒有交易序號，無法分辨是否為真實的多筆交易；不屬於本 issue，在 LEDGER 剖析基線列為「合法（上游如此）」。

## 根因

L1：視窗長度固定 31 天，沒有對齊取樣間隔。凍結在 pop 的回應已經含重疊日，所以要修正規化層才能清掉既有的重複。

## 同類掃描

其他「每月取樣、每次回一段期間」的表，用各自的實體鍵查跨 as_of 重複（`snapshot.py` 的 `ENTITY_KEYS`）：`institutional_net_buy_sell`、`company_valuation`、`foreign_ownership`、`monthly_revenue_twse`、`dividend` 都是 0。`finmind_news` 的跨 as_of 重複只有 1 個 link（CMoney 個股概覽頁，每天都出現，不是視窗重疊），見 I018。

## 修復方案（建議 A）

- **A（L5，0 次上游呼叫）**：`db/poc/sbl_history.sql` 加 `WHERE custom.parse_iso_date(record->>'completion_date') > as_of - INTERVAL '1 month'`，再用 §10.5 的 `pin_seed_responses` → `update_mat_views(['sbl_history'])` → `unpin_responses` 重建，預計刪掉 296 列。規格相容（欄位不變），就地修改，不需要 `_v2`。限制：某個月份沒爬時，下一個月不會補回重疊日（目前 2330 249 個月連續、2317／6488 18 個月連續）。
- **B（L1）**：adapter 的視窗起點改成「上一個取樣日 + 1 天」（`as_of − 1 個月 + 1 天`），新爬的列不再重疊；既有 287 個 seed 列要重爬 TWSE（約 287 次，超過 §8.3 每題 120 的上限，要分批）。
- A 與 B 可以並用：A 清既有資料，B 讓 raw 回應本身乾淨。
- 測試：`tests/sql/` 補一個檢查 `sbl_history.sql` 含視窗過濾的測試；Q009 C1 是回歸防線。

## db/poc 規則檢查（方案 A）

| 規則 | 檢查 | 結果 |
| --- | --- | --- |
| rule 1、3（非 `_list` 不可有 mutable 操作） | `date - interval` 是 immutable 運算子；不新增 `::DATE` | 符合；實作後用 `pop_check_definitions` 確認 |
| rule 6、7（列唯一、主鍵來自 `_list`） | 只刪列，不改主鍵與欄位 | 符合 |
| rule 10（正規化層依資料可用性整理） | 過濾的是重疊視窗造成的重複，不是隱藏資料邊界（rule 13 管的是 `_list`） | 符合 |
| rule 16（只用 INNER／CROSS JOIN） | 沒有新增 JOIN | 符合 |
| rule 18（規格變動要 `_v2`） | 欄位、主鍵、上游都不變，屬「只修 bug」 | 就地修改 |
| rule 22（JSON 路徑包在函式裡要加括號） | `custom.parse_iso_date(record->>'completion_date')` 是單層 `->>` | 符合；跑 `pytest tests/sql/test_sql_column_lineage_coverage.py` |
| 文件 | `db/poc/README.md` 的 `sbl_history_monthly_list` 段（「月度取樣視窗首尾相接、無重複」）不成立 | 要一起改 |
