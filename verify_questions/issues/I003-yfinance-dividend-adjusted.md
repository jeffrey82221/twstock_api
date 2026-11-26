---
id: I003
title: dividend_yfinance 的現金股利是配股還原後的值，與實際配發金額不一致
severity: S1
layer: L1（部分 L0）
status: closed
pop_tables: [dividend_yfinance, raw_dividend_yfinance, raw_dividend_history_yfinance]
seeds: [dividend_event_yfinance_list]
found_by: [Q005]
---

## 症狀（附可重現的 SQL）

FinMind 與 yfinance 同一除息日的現金股利，41 組可比對、3 組不符，且比值固定：

| stk_code | 除息日 | FinMind | yfinance | 比值 |
| --- | --- | ---: | ---: | ---: |
| 1101 | 2021-08-12 | 3.368 | 3.062 | 1.100 |
| 2882 | 2021-07-26 | 2.500 | 2.461 | 1.016 |
| 2882 | 2022-07-04 | 3.500 | 3.446 | 1.016 |

比值固定、只出現在之後有配股的公司，推測 yfinance 依之後的配股做了還原調整。`dividend` 與 `dividend_yfinance` 欄位名稱相同（`cash_dividend`），語意卻不同（S1）。

```sql
SELECT f.stk_code, f.reference_date, f.cash_dividend AS finmind, y.cash_dividend AS yfinance,
       round(f.cash_dividend / NULLIF(y.cash_dividend, 0), 4) AS ratio
FROM pop.dividend f JOIN pop.dividend_yfinance y USING (stk_code, reference_date)
WHERE abs(f.cash_dividend - y.cash_dividend) > 0.01
```

## 證據
- 1101：FinMind 記錄 2022-07-21 配股 0.999 元（= 10%）；Yahoo `Ticker.splits` 有 2022-07-21 = 1.0999282。2021-08-12 現金股利 3.0621 × 1.0999 = 3.368 = FinMind 實際值。
- 2882：比值 1.0157，但 Yahoo 在 2020 年後**沒有任何 split 紀錄**，推測為現金增資等其他還原調整。
- [`app/yfinance_source.py`](../../app/yfinance_source.py) 直接把 `Ticker.dividends` 當成「每股現金股利（元）」，註解寫「`Ticker.actions` 額外多一欄 stock split，這裡只取 dividends」。

## 根因

Yahoo 的 `Ticker.dividends` 是依之後的配股（記為 split）與其他資本事件還原調整過的值，adapter 當成實際配發金額輸出。配股部分可用 `Ticker.splits` 反推（L1）；沒有 split 紀錄的調整（2882）無法從 Yahoo 反推（L0）。

## 修復方案

1. `app/yfinance_source.py`：取 `Ticker.splits`，每筆股利乘上「除息日之後所有 split 比例的乘積」還原成實際金額；回應加 `adjusted_for_splits: true`。
2. 無法反推的情況（2882）列為 L0 已知限制，寫進 Q005「已知限制」與 `db/poc/README.md` 的 dividend_yfinance 章節：實際配發金額以 FinMind（`dividend`）為準。
3. 重爬：只需 `dividend_event_yfinance_list` 中「除息日之後有 split」的股票的列；需先列出受影響股票（Yahoo 逐檔查 splits）。

## 修復紀錄（2026-11-25，指令 5）

- `app/yfinance_source.py`：新增 `split_unadjust_factors()`；每筆股利乘上「除息日之後所有 split 比例的乘積」還原成實際配發金額，rows 加 `SplitUnadjustFactor`；模組說明同步。`db/poc/README.md` 的 dividend_yfinance 章節註明「實際配發金額以 FinMind 為準」。
- 測試：`tests/source/test_yfinance_dividend_source.py`（新檔，3 個）。

## 重爬與驗證

- 直接呼叫：1101 2021-08-12 → 3.368119（FinMind 3.36811984）。
- 受影響範圍：`dividend_event_yfinance_list` 296 檔中 126 檔在某些事件之後有 split；刪除並重爬這些檔「最後一次 split 之前」的事件 163 列（51 秒、0 錯誤、每次刪除都通過 1:1 驗證）。
- 斷言：Q005 C1 違規 3 → 2。剩下的 2 列都是 2882（比值 1.0157、Yahoo 沒有 split 紀錄）＝ **L0**，無法在本系統修正。
- **待使用者決定**：接受 L0 殘留（把 2882 寫進 Q005「已知限制」並讓 C1 排除沒有 split 紀錄的調整），或維持 FAIL 作為「yfinance 股利不可當實際配發金額」的提醒。

## 使用者決定（2026-11-25）

**維持 FAIL** 作為「yfinance 股利不可當實際配發金額」的提醒。L1 部分（配股還原）已修好；2882 的 1.0157 倍差異是 Yahoo 本身的資料（L0），本系統不再處理。
- 本 issue 關閉；Q005 C1 維持 FAIL 是**刻意的**，回歸時 C1 的 2882 違規不算退化，其他股票出現新的違規才算。
- 決策用途的實際配發金額一律以 FinMind（`pop.dividend`）為準。
