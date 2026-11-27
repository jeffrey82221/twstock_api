---
id: I025
title: company_valuation 丟掉 BWIBBU 的「財報年/季」與「股利年度」，無法判斷 PER／PBR 用的是哪一季財報
severity: S3                 # 值本身沒錯，但缺了解讀所需的欄位：PBR 換季時的跳動與 I020 的 EPS 基準都無法驗證
dimension: 完整性
layer: L1
status: diagnosed
pop_tables: [raw_company_valuation, company_valuation]
seeds: [company_valuation_monthly_list]
found_by: [Q016（指令 2 出題）]
---

## 症狀（附可重現的 SQL）

出 Q016 時想寫合理性斷言「同一份財報基準下，隱含每股淨值（收盤價 ÷ PBR）不變，只差 PBR 的捨入」。用公告期限（3/31、5/15、8/14、8/31、11/14）判斷換季，全表 1,065 組同基準的相鄰月份中有 49 組違規：

```sql
WITH v AS (
  SELECT stk_code, as_of, data_date, close_price, pbr,
         lag(close_price) OVER w AS pc, lag(pbr) OVER w AS ppbr, lag(data_date) OVER w AS pd
  FROM pop.company_valuation WHERE pbr > 0 AND close_price > 0 WINDOW w AS (PARTITION BY stk_code ORDER BY as_of)
)
SELECT stk_code, pd, data_date, pc, ppbr, close_price, pbr, round(((close_price / pbr) / (pc / ppbr) - 1)::numeric, 4) AS chg
FROM v WHERE pc IS NOT NULL AND data_date - pd <= 40
ORDER BY abs((close_price / pbr) / (pc / ppbr) - 1) DESC LIMIT 20
```

例：1232 2017-07-05 → 08-04 每股淨值變動 −15%，期限 8/14 還沒到——公司提早公告，TWSE 在公告當天就換了基準。實際換季日只有 BWIBBU 的「財報年/季」欄知道。

## 證據

- TWSE BWIBBU_d 原始回應（`www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20260805&selectType=ALL`、`date=20260904`，2026-11-27 19:00）欄位：`[證券代號, 證券名稱, 收盤價, 殖利率(%), 股利年度, 本益比, 股價淨值比, 財報年/季]`；2882 8/5 是 `115/1`、9/4 是 `115/2`（PBR 2.01 → 1.66，隱含每股淨值 50.0 → 69.3）。
- `pop.raw_company_valuation` 2882 as_of 2026-09-05 的 `constituent`：有 `close_price`、`per`、`pbr`、`dividend_yield_pct`、`eps_ttm`、`bvps`、`dps` 等，**沒有** `financial_report_period`、`dividend_year`。
- [`app/valuation_source.py`](../../app/valuation_source.py)：BWIBBU 解析時有讀這兩欄（294、297、326 行；舊版面 262 行設成 `None`），但組 constituent 時（400–418 行）沒有放進回應。

## 根因

L1：adapter 解析了「財報年/季」與「股利年度」，但 `company_valuation` 的回應（constituent）沒有輸出，正規化表也就沒有這兩欄。

影響：
- PER／PBR 換季時的跳動無法和錯值區分（上表 49 組都可能是提早公告，也可能有解析錯）。
- I020（2882 TWSE 隱含 EPS 比 MOPS 近四季低 6–7%）的假說「TWSE 用的 EPS 基準不同」需要這一欄才能確認。
- `dividend_yield_pct` 用的是哪一年度的股利也無法對照 Q014 的除息資料。

## 同類掃描

失效模式：「adapter 有解析但沒輸出的上游欄位」。

| 範圍 | 結果 |
| --- | --- |
| `market_valuation_summary` | 由個股加總，不需要財報季；不適用 |
| 其他 adapter | 沒有系統性掃描；指令 5 修復時用 `grep -n '"[a-z_]*": None' app/*source*.py` 對照各 response 欄位再看一次 |

## 修復方案

- **改哪些檔**：`app/valuation_source.py` constituent 加 `financial_report_period`、`dividend_year`；`db/poc/company_valuation.sql` 加兩欄（欄位規格變動 → 依 rule 18 新增 `company_valuation_v2.sql`，或確認可就地加欄）。
- **測試**：`tests/source/test_valuation_source.py` 斷言 constituent 有這兩欄；`pytest tests/sql/ -q`。
- **影響的既有列**：`company_valuation` 全部 1,684 列（raw 存的回應沒有這兩欄，要重爬才有）。IMMV 重建等於對每個 seed 列重打 BWIBBU（同一天全市場一份，磁碟快取命中率高），先 `pop_update_mat_views(dry_run=true)` 看 `seed_upstreams`。
- **重打 API 次數**：最多 1,684 次 API 請求；BWIBBU 以日期為單位快取，實際打 TWSE 的次數推估約等於不同交易日數（沒有實測）。
- **db/poc 規則檢查**：動 SQL，指令 5 依 §10.1 填寫（rule 18 為重點）。
- **復發報警**：Q016 補上「同一財報季內隱含每股淨值不變」的合理性斷言。

## db/poc 規則檢查

## 修復紀錄

## 重爬與驗證
