---
id: I019
title: 金融股在 Q3 期限當天還拿不到 Q3，adapter 回上一季，被凍結成重複的季度
severity: S2
dimension: 唯一性／及時性
layer: L1（fallback 到舊季度）＋ 待確認 L0／L3（金融業期限）
status: diagnosed
pop_tables: [raw_mops_quarterly_financials, mops_quarterly_financials]
seeds: [mops_quarterly_financials_quarter_list]
found_by: [2026-11-27 指令 1 維度剖析, Q010]
---

## 症狀（附可重現的 SQL）

```sql
SELECT stk_code, fiscal_year, quarter, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.mops_quarterly_financials WHERE fiscal_year IS NOT NULL
GROUP BY 1, 2, 3 HAVING count(*) > 1
```

2882 2026Q2 出現兩次：as_of 2026-08-31（Q2 期限）與 2026-11-14（Q3 期限）。11-14 那列應該是 Q3。

## 證據

- 直接呼叫 API（2026-11-27）：`/api/company/2882/quarterly-financials?as_of=2026-11-14` 與 `?as_of=2026-11-27` 都回 `fiscal_year 2026、quarter 2、available_date 2026-08-31`。到今天 MOPS t163sb04 仍查不到 2882 的 2026Q3。
- `app/mops_quarterly_financials._scan_quarters`：查不到當季就往前一季找，最多 `MAX_LOOKBACK_QUARTERS` 季，所以「Q3 還沒上 MOPS」會回 Q2，而且 `found=true`。
- `app/pit.py` 對金融業只延長 Q2 期限（8/31），Q1／Q3 仍是 45 天（5/15、11/14）。2025 年的 2882 Q1／Q3 都拿得到，但那些列是事後才爬的，無法證明期限當天已公告。
- seed 每季在期限當天產生一列；這一列一旦在上游公告前被爬，就永遠凍結成上一季（與 I016 同型：失敗被當成成功）。

## 根因

L1：拿不到應有的季度時，用舊季度冒充。是否還有 L3（金融業 Q1／Q3 的法定期限比 45 天長），要對照法規或 MOPS 實際公告日才能確定。

## 同類掃描

`ENTITY_KEYS` 剖析：其他股票、其他季度都沒有重複季度（1 組）。目前只有 2882 一檔金融股有季報資料。

## 修復方案

1. L1：期限已過但當季還沒上 MOPS 時，回 `found=false`、`reason=not_yet_published`，不要回舊季度。`retry_failed.py` 之後會重爬。
2. 確認金融業 Q1／Q3 的公告期限；若比 45 天長，`app/pit.py` 與 seed（`mops_quarterly_financials_quarter_list.sql`）要一起改（I002 的 PIT 規則）。
3. 修好後用 `crawl_delete_rows` 刪掉 2882 as_of 2026-11-14 這一列並重爬（1 列、MOPS 約 4 次呼叫）。
4. Q010 C3（唯一性）是回歸防線。
