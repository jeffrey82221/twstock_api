---
id: I004
title: 正規化 view 的 as_of 取自 API 回應，回應失敗時主鍵變成 NULL
severity: S2
layer: L5
status: closed
pop_tables: [company_valuation, dividend, dividend_yfinance, finmind_news, institutional_net_buy_sell, market_valuation_summary, monthly_revenue, mops_quarterly_financials, financial_quarterly_yfinance, financial_yearly_yfinance]
seeds: []
found_by: [Q001]
---

## 症狀（附可重現的 SQL）

`company_valuation.sql` 以 `custom.parse_iso_date(company_valuation->>'date') AS as_of` 取主鍵，回應為 NULL 時 as_of 也是 NULL。
6488 的 43 列 `company_valuation` 全部 `as_of IS NULL`：以日期為鍵的查詢與斷言完全看不到這些失敗列（Q001 原本的 C2 因此誤判 PASS）。

同樣寫法（as_of 取自回應 JSON）的正規化 view 共 10 張：`company_valuation`、`dividend`、`dividend_yfinance`、`finmind_news`、
`institutional_net_buy_sell`、`market_valuation_summary`、`monthly_revenue`、`mops_quarterly_financials`、
`financial_quarterly_yfinance`、`financial_yearly_yfinance`。

```sql
SELECT 'company_valuation' AS t, count(*) FILTER (WHERE as_of IS NULL) AS null_key_rows, count(*) AS total FROM pop.company_valuation
UNION ALL SELECT 'institutional_net_buy_sell', count(*) FILTER (WHERE as_of IS NULL), count(*) FROM pop.institutional_net_buy_sell
UNION ALL SELECT 'finmind_news', count(*) FILTER (WHERE as_of IS NULL), count(*) FROM pop.finmind_news
```

## 證據
- 10 張正規化 view 以回應 JSON 的日期當 as_of；raw 表本身都有 seed 的 as_of（或 `valuation_date`）。
- 成功列中「回應日期 ≠ seed 日期」的列數：**10 張全部為 0**（逐一比對）→ 改用 seed 欄位不會改變任何既有值，只會補上失敗列的主鍵。
- 目前實際受影響：`company_valuation` 211 列（上櫃股，I005）、`mops_quarterly_financials` 12 列（2882，I009）。`finmind_news` 的 26 個失敗列因 LATERAL 攤平本來就是 0 列，不受影響；其他 7 張目前沒有失敗列（潛在問題）。

## 根因

正規化層把主鍵取自 API 回應而非 seed，違反「`_list.sql` 的欄位即為下游 view 的 primary key」（db/poc rule 7）。L5。

## 修復方案

1. 10 張 view 的 `as_of` 改為 raw 的 seed 欄位（`raw.as_of`；`market_valuation_summary` 用 `valuation_date`）。rule 18：欄位、主鍵、成功列的值都不變 → 就地修正。
2. 上線：`poc_rebuild_views` → `pop_check_definitions` → `pop_update_mat_views`（IMMV DROP + 重建 = 對既有 seed 列重打 API）。重打成本：company_valuation 1,684（TWSE BWIBBU_d 依日期磁碟快取，約 115 個日期）、mops 355（MOPS 磁碟快取）、dividend 57 + finmind_news 95 + monthly_revenue 1（FinMind，共 153，須在 300 req/hr 內）、yfinance 三張共 2,366、institutional 682、market 115。
3. **分批**：第一批只做實際受影響的 `company_valuation`、`mops_quarterly_financials`（與 I001 一起重建）；FinMind 三張待額度空檔、yfinance 三張另排時段。

## 修復紀錄（2026-11-25，指令 5）

- 第一批（實際有失敗列的 2 張）：`db/poc/company_valuation.sql`、`db/poc/mops_quarterly_financials.sql` 的 `as_of` 改取 raw 的 seed 日期；`db/poc/README.md` 同步。`tests/sql/` 197 passed（含 lineage）。
- 上線：`pop_update_mat_views` 重建兩個 IMMV（18 秒；company_valuation 1,684 列走 backend 磁碟快取）。
- **第二批尚未做**：`dividend`、`dividend_yfinance`、`finmind_news`、`institutional_net_buy_sell`、`market_valuation_summary`、`monthly_revenue`、`financial_quarterly_yfinance`、`financial_yearly_yfinance`（目前 0 列受影響）。重建會對既有 seed 列重打 API，其中 FinMind 三張共 153 次，須排在 FinMind 額度空檔。

## 重爬與驗證

- `company_valuation`：NULL 主鍵 211 → 0。Q001 C1 PASS。
- 附帶發現：`pop_check_definitions` 顯示 `sbl_history`、`monthly_revenue_twse` 也是 `changed`，是本次之前就存在的 SQL 與 pop 不一致（本次未動）。

## 修復紀錄（2026-11-26，第二批）

- 8 張 view 的 `as_of` 改取 raw 的 seed 欄位：`dividend`、`dividend_yfinance`、`finmind_news`、`institutional_net_buy_sell`、`monthly_revenue`、`financial_quarterly_yfinance`、`financial_yearly_yfinance` 用 `raw.as_of`，`market_valuation_summary` 用 `valuation_date`。`db/poc/README.md` lineage 同步（順便把 `monthly_revenue_twse` 的 lineage 更正為 `raw_monthly_revenue_twse.as_of`）。
- 上線前重新逐表確認「成功列回應日期 ≠ seed」= 0（institutional 的回應 `trade_date` 就是請求日，實際交易日在 `row.trade_date` 欄）→ rule 18 就地修正。
- 新增 `pop_maintenance.pin_seed_responses()`／`unpin_responses()`：把 raw IMMV 已存的回應 pin 進 `http_cache`，再 `Pipeline.update_mat_views([name])` 重建正規化 IMMV。重建因此逐字使用 raw 的回應，**不打上游**、raw 與正規化不會分歧（I011）。測試：`tests/pipeline/test_pop_maintenance.py::test_pin_seed_responses_serves_stored_raw_responses`。

## 重爬與驗證（第二批）

| IMMV | pinned | 列數 前 → 後 | NULL as_of | http_log |
| --- | ---: | --- | ---: | ---: |
| market_valuation_summary | 115 | 115 → 115 | 0 | +0 |
| dividend_yfinance | 460 | 460 → 460 | 0 | +0 |
| institutional_net_buy_sell | 533 | 533 → 533 | 0 | +0 |
| financial_yearly_yfinance | 769 | 769 → 769 | 0 | +0 |
| dividend | 51 | 51 → 51 | 0 | +0 |
| monthly_revenue | 1 | 1 → 1 | 0 | +0 |
| finmind_news | 95 | 1,955 → 1,955 | 0 | +0 |
| financial_quarterly_yfinance | 562 | 562 → 562 | 0 | +0 |

`pop_definition_drift` 全部 ok；`run_checks.py --all` 結果不變（Q001–Q004 PASS，Q005 僅 C1）。

