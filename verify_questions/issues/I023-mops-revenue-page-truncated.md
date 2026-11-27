---
id: I023
title: MOPS t21sc03 月營收彙總頁被截斷時，adapter 當成完整頁並快取，截斷點之後的公司少一個月（重複上一月、TTM 跨 13 個月）
severity: S1                 # 缺月本身是 S2，但 TTM／TTM YoY 會算錯成看起來正常的值
dimension: 完整性
layer: L0＋L1
status: diagnosed
pop_tables: [raw_monthly_revenue_twse, monthly_revenue_twse]
seeds: [financial_month_twse_list]
found_by: [Q008]
---

## 症狀（附可重現的 SQL）

Q008 C3 的可比對列數是 12，預期 15（5 檔 × 3 個月）。少掉的 3 組是 2308、2882、3045 的民國 115/09：`monthly_revenue_twse` 在 as_of 2026-10-10 回的是 **2026/08**，和 as_of 2026-09-10 同一個月：

```sql
SELECT stk_code, latest_month_label, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.monthly_revenue_twse WHERE latest_month_label IS NOT NULL
GROUP BY stk_code, latest_month_label HAVING count(*) > 1
```

2026-11-27 18:20 執行：3 組（2308、2882、3045，as_of 2026-09-10 與 2026-10-10）。同一批補爬的 1402、1519 在 as_of 2026-10-10 正確拿到 2026/09。

## 證據

1. **API 本身就回 8 月**：`curl 'localhost:5002/api/company/2308/revenue/twse?as_of=2026-10-10'` → `latest_month_label: 2026/08`、`available_date: 2026-09-10`、`source: MOPS t21sc03 (history)`。`pit.latest_available_revenue_month(2026-10-10)` 是 2026/09，所以不是 PIT 規則擋掉的。
2. **MOPS 9 月彙總頁被截斷**（2026-11-27 18:20–18:40，`verify_questions/upstream_peek.py t21sc03 sii:115:9 …`，9 月那頁連續抓 3 次結果相同）：

   | 頁面 | HTTP | bytes | 解析到的公司數 | 產業段數 | 2330／2308 在頁內 | 結尾 | Last-Modified（GMT） |
   | --- | --- | ---: | ---: | ---: | --- | --- | --- |
   | `sii/t21sc03_115_8_0.html` | 200 | 456,059 | 997 | 33（水泥 … 存託憑證） | 是／是 | `</table></body></html>` | 11-27 09:59:04 |
   | `sii/t21sc03_115_9_0.html` | 200 | 133,276 | 284 | 13（水泥 … 汽車工業） | 否／否 | `…<td align=center>1522</td><td align=left`（斷在一列中間） | 11-27 09:59:23 |
   | `sii/t21sc03_115_10_0.html` | 200 | 457,065 | 997 | 33 | 是／是 | 完整 | 11-27 10:07:31 |
   | `sii/t21sc03_114_9_0.html` | 200 | 451,720 | 996 | 33 | 是／是 | 完整 | 11-27 05:28:43 |

   MOPS 每天重新產生這些靜態頁；9 月那頁在台北 17:59 這次產生到一半就停了。回應標頭的 `Content-Length` 是 20,495（gzip 後），表示伺服器送的就是截斷後的檔案，不是傳輸中斷。
3. 本輪補爬在 18:16 左右，剛好落在截斷頁產生之後。

## 根因

- **L0**：MOPS 的 `t21sc03_115_9_0.html` 靜態檔在 2026-11-27 的重新產生中被截斷（HTTP 200）。可能是暫時性的，下次重新產生會恢復，但無法預期。
- **L1**：[`app/sources.py`](../../app/sources.py) `_fetch_mops_revenue_page`（567–635 行）只要 HTTP 200 就把解析結果寫進 24 小時的 `_mops_revenue_history_cache`，不檢查頁面是否完整。`get_mops_revenue_history` 再把「公司不在表中」當成「該月沒資料，跳過」（661 行的 docstring 明寫），`query_revenue_twse` 沒有任何 `source_errors`，回應是 `found=true`，pop 就凍結了。

後果（截斷點之後約 713 家上市公司，任何 26 個月視窗包含 115/09 的 as_of）：

- **缺月＋重複**：as_of 2026-10-10 回 2026/08（Q002 類 YoY 連續月數會重複算 8 月、漏掉 9 月）。
- **TTM 錯值**：`eligible[:12]` 少一個月就往前多拿一個月，as_of 2026-11-10 的 `ttm_value` 是 2025/10–2026/10 扣掉 2026/09 的 12 個月；`ttm_yoy_pct` 的兩段也都錯位。值看起來正常，是 S1。
- 同一個 24 小時內的 API 呼叫（含 cron）全部吃到同一份截斷 cache。

## 同類掃描

失效模式：「上游 HTTP 200 但內容不完整，被當成成功並快取／凍結」（I010 是同一家族：失敗被快取成空）。

| 範圍 | 結果 |
| --- | --- |
| `monthly_revenue_twse` 實體重複（`stk_code, latest_month_label`） | 3 組，全部是本輪補爬的列（上表 SQL）；2026-11-27 00:04 的剖析是 0 |
| `monthly_revenue_twse` 本輪補爬的其他列 | 1402、1519 正確；2308、2882、3045 的 as_of 2026-11-10（標籤 2026/10 正確，但 TTM 視窗缺 9 月）也受影響，共 6 列 |
| 其他抓 MOPS 靜態彙總頁的 adapter | `grep -n "nas/t21\|MOPS_HISTORY_BASE" app/*.py`：只有 `_fetch_mops_revenue_page`。JSON 類上游（TWSE OpenAPI、BWIBBU）截斷時的行為沒有實測，指令 5 修復時順便確認 |

## 修復方案

- **改哪些檔**：`app/sources.py` `_fetch_mops_revenue_page`：解析前檢查頁面完整（結尾有 `</html>`，且最後一個產業段之後有合計列）；不完整時記 `SourceError`（MOPS、`truncated page`）、**不寫 cache**、回空 dict。`query_revenue_twse` 因此帶 `source_errors`，`retry_failed.py raw_monthly_revenue_twse` 會選到它。
- **測試**：`tests/source/test_revenue_twse_source.py` 加一個截斷的 HTML fixture（取 115_9 的前 133,276 bytes 的縮小版），斷言回空、記了 source error、cache 沒有這個 key。
- **_v2 SQL**：不需要。
- **影響的既有列**：6 個 `financial_month_twse_list` seed 列（2308、2882、3045 × as_of 2026-10-10、2026-11-10）。
- **重打 API 次數**：每列 1 次 t187ap05（有 cache）＋ 26 頁 MOPS（同一批共用 cache），約 30 次 MOPS GET。先確認 `t21sc03_115_9_0.html` 恢復完整再重爬，否則修好的 adapter 會回 source_errors，列一樣不可用。
- **復發報警**：§7.5 剖析的 `monthly_revenue_twse` 實體鍵已經能抓到（下一輪指令 1 會報 3 組新重複）；指令 5 另在 Q002 加一個 `uniqueness` 斷言（同一檔同一營收月只有一列）。

## db/poc 規則檢查

不動 SQL，不適用。

## 上游恢復（2026-11-27 第 6 輪）

- 19:05 `upstream_peek.py t21sc03 sii:115:9`：456,350 bytes、997 家、33 個產業段、結尾完整，Last-Modified 10:58:31 GMT（台北 18:58，MOPS 下一次重新產生）。截斷持續約 1 小時。
- API 的 `_mops_revenue_history_cache`（記憶體、24 小時）還留著截斷頁：重啟 uvicorn 後，`/api/company/2308/revenue/twse?as_of=2026-10-10` 回 **2026/09、65,399,563,000 元**（重啟前回 2026/08）。
- pop 的 6 列仍是錯的（凍結），留給指令 5 和 adapter 修正一起重爬；重爬前不需要再等上游。
- 教訓：上游恢復後，API 程序內的快取仍會讓錯誤延續最多 24 小時，cron 在這段時間爬的列一樣會錯。修 adapter 時「不完整頁不寫 cache」可以同時解決。

## 修復紀錄

## 重爬與驗證
