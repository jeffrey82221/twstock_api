---
id: I022
title: MOPS 產品營收的負數項目（產品別調整、金融業損失科目）被 adapter 丟掉，產品合計大於營收淨額
severity: S2                 # 列存在的每個值都對，但少了負數列；合計／占比加總類的決策會高估
dimension: 完整性
layer: L1
status: diagnosed
pop_tables: [raw_product_revenue, product_revenue]
seeds: [product_revenue_filer_list]
found_by: [Q008]
---

## 症狀（附可重現的 SQL）

[Q008](../questions/Q008-product-revenue-concentration.md) C1（各產品金額合計 − 銷貨退回 = 營收合計）在 5 檔中 10 組 (stk_code, ym) FAIL；全表 180 組中 21 組、13 檔違規，**全部是合計大於營收淨額**，沒有一組是不足：

```sql
SELECT stk_code, ym, sum(amount) AS s, max(sales_return) AS sr, max(total_revenue) AS tot,
       round(100 * (sum(amount) - coalesce(max(sales_return), 0) - max(total_revenue)) / max(total_revenue), 2) AS gap_pct
FROM pop.product_revenue GROUP BY 1, 2
HAVING abs(sum(amount) - coalesce(max(sales_return), 0) - max(total_revenue)) > 0.01 * abs(max(total_revenue))
ORDER BY gap_pct
```

gap 從 1535 民國 113/11 的 1.57% 到 2891 民國 110/09 的 349%；金融業（2880、2882、2891、2820、2836、2838、2855、2889）差距最大。

## 證據

直接打 MOPS t05st08（2026-11-27 18:19，繞過 adapter 解析：`.venv/bin/python verify_questions/upstream_peek.py t05st08 1519:11206 2882:11510 1402:11510`）：

| 公司 | 民國年月 | MOPS 上的負數列（仟元） | 負數合計 | DB 的缺口 |
| --- | --- | --- | ---: | ---: |
| 1519 華城 | 112/06 | (9) 配電盤 −29,043、(10) 配電器材 −59,269；備註「負數為產品別調整」 | −88,312 | 1,491,747 − 1,403,435 = 88,312 ✓ |
| 2882 國泰金 | 115/10 | (7) −190,422、(8) −670,597、(9) 保險財務收益或費用 −6,047,735、(10) −16,021,750、其他 −15,064,349 | −37,994,853 | 60,925,309 − 22,930,456 = 37,994,853 ✓ |
| 1402 遠東新 | 115/10 | 無 | 0 | 0（C1 通過）✓ |

缺口與 MOPS 的負數合計分毫不差。

## 根因

L1：[`app/sources.py`](../../app/sources.py) `_parse_mops_table` 只收 `c3_amount > 0` 的產品列（926 行）：

```python
if c2 and c3_amount is not None and c3_amount > 0:
```

MOPS 的產品列可以是負數：一般公司是「產品別調整」（MOPS 備註原文），金融業是損失科目（金融資產評價損失、保險財務費用）。丟掉之後，剩下每一列的金額與占比本身仍然正確（占比分母是營收淨額＋銷貨退回），但：

- 各列合計 ≠ 營收淨額，占比加總 > 100%（金融業可達 449%）；
- 金融業「第一大產品占比」會超過 100%（2882 利息淨收益 127.54%），[Q008](../questions/Q008-product-revenue-concentration.md) 的集中度門檻對金融業失去意義；
- 想用產品明細重建營收（例：某產品線年增）的決策會高估。

正規化 SQL（`db/poc/product_revenue.sql`）沒有過濾，攤平全部 items，不是 L5。

## 同類掃描

失效模式：「adapter 只收正數，把上游合法的負數列丟掉」。

| 範圍 | 結果 |
| --- | --- |
| `product_revenue` 全表 | 21／180 組、13 檔（上表 SQL） |
| 其他 adapter 是否有 `> 0` 過濾 | `grep -n "> 0" app/*source*.py app/sources.py app/service.py`：其餘都是除數保護（`valuation_source.py` 的 PER／PBR > 0 才反推、市場加總 > 0 才相除）或 split 比例，丟掉資料列的只有這一處 |

沒有掃到其他根因。

## 修復方案

- **改哪些檔**：`app/sources.py` 926 行改成 `c3_amount is not None and c3_amount != 0`（保留負數，空白與 0 仍跳過）；占比照舊用 `amount / (total_revenue + sales_return)`，負數列得到負的占比。
- **測試**：`tests/source/` 新增 1519 112/06 的 HTML fixture（含兩個負數列與「負數為產品別調整」備註），斷言 items 有 5 列、合計 − 銷貨退回 = 營收淨額。
- **_v2 SQL**：不需要（只改 L1，欄位規格不變）。
- **影響的既有列**：21 個 `product_revenue_filer_list` seed 列；raw 與正規化都是 1:1 對 seed 的 filing。
- **重打 API 次數**：每列 2～3 次 MOPS（申報清單有 cache，明細 1 次），約 45 次，在 §8.3 MOPS 60 的預算內，分 3 批（`row_cnt ≤ 8`）。
- **斷言**：[Q008](../questions/Q008-product-revenue-concentration.md) C2 已改成不限制單項占比範圍（見 [Q008](../questions/Q008-product-revenue-concentration.md) 已知限制）；修好後 [Q008](../questions/Q008-product-revenue-concentration.md) C1 應該 0 違規，它就是復發時的報警。
- **決策面**：[Q008](../questions/Q008-product-revenue-concentration.md) 的「單一產品 > 70%」門檻要排除金融業，或改用「正數項目中的占比」。修好後由使用者決定。

## db/poc 規則檢查

不動 SQL，不適用。

## 修復紀錄

## 重爬與驗證
