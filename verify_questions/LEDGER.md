# Ledger

> 由 AI 指令維護（見 [README §6.4](README.md#64-ledgermd)）。手動修改也可以，AI 會以這裡的內容為準。

## 問題

| ID | 策略 | 標題 | 狀態 | 最後執行 | 未結 issue |
| --- | --- | --- | --- | --- | --- |
| [Q001](questions/Q001-valuation-per-percentile.md) | 估值 | 2330、2317、6488 本益比在近 3 年第幾百分位 | passing | 2026-11-26 | — |
| [Q002](questions/Q002-growth-revenue-momentum.md) | 成長 | 2330、2454、6488 月營收 YoY 連 3 月 > 20%，季營收對得起來 | passing | 2026-11-26 | — |
| [Q003](questions/Q003-quality-roe-debt.md) | 體質 | 2330、2882、1101 連 3 年 ROE > 15% 且負債比 < 50% | passing | 2026-11-26 | — |
| [Q004](questions/Q004-flow-foreign-institutional.md) | 籌碼 | 2330、2317、6488 外資持股、法人買賣超、借券趨勢 | passing | 2026-11-26 | — |
| [Q005](questions/Q005-income-dividend-consistency.md) | 股利 | 2330、2317、2882、1101 連 5 年配息與殖利率 | failing（C1 刻意保留） | 2026-11-26 | I003（使用者決定保留 FAIL 作提醒） |
| [Q006](questions/Q006-market-per-percentile.md) | 市場 | 上市大盤本益比在近 5 年第幾百分位 | passing | 2026-11-27 | — |
| [Q007](questions/Q007-quarterly-eps-yfinance-vs-mops.md) | 成長 | 2330、2454、6488、2882、7769 近四季 EPS 年增；yfinance vs MOPS 季 EPS | draft | — | I016 |
| [Q008](questions/Q008-product-revenue-concentration.md) | 產業 | 1402、2308、3045、2882、1519 產品營收集中度；產品營收 vs 月營收 | failing | 2026-11-27 | I022 |
| [Q009](questions/Q009-sbl-fee-pressure.md) | 籌碼 | 2330、2317、6488 近 3 個月借券費率是否明顯上升 | passing | 2026-11-27 | — |
| [Q010](questions/Q010-financial-holding-valuation.md) | 估值 | 金控 2882、2891、2886 PER 是否低於近 3 年中位數 | draft | — | I019、I020 |
| [Q011](questions/Q011-technical-ma60-volatility.md) | 技術 | 2330、2317、6488 是否站上 60 日均線、20 日波動度 | passing | 2026-11-27 | I021（每次執行前重爬當月） |
| [Q012](questions/Q012-annual-eps-accuracy.md) | 體質 | 2330、2317、2454 年度 EPS 是否連續成長；EPS 對照公司公布值（第一個 `accuracy`） | passing | 2026-11-27 | — |
| [Q013](questions/Q013-monthly-revenue-two-sources.md) | 成長 | 2330、2317、1402 月營收年增率是否轉強；FinMind vs TWSE／MOPS | passing | 2026-11-27 | — |
| [Q014](questions/Q014-cash-dividend-accuracy.md) | 股利 | 2330、2317、2882、1101 現金股利是否逐年提高；股利對照 TWSE 除息結果 | draft | — | I024 |
| [Q015](questions/Q015-flow-foreign-ratio-internal.md) | 籌碼 | 2454、2882、1101 外資持股比是否上升；股數／比率／上限內部一致 | passing | 2026-11-27 | — |
| [Q016](questions/Q016-valuation-pbr-percentile.md) | 估值 | 2454、2882、1101 PBR 在近 18 個月第幾百分位；估值收盤價 vs 日 K | passing | 2026-11-27 | — |
| [Q017](questions/Q017-basic-info-capital-listing.md) | 產業 | 2330、2454、6488、7769、2317 掛牌年數、資本額、產業鏈；資本額 vs 發行股數 | passing | 2026-11-27 | — |
| [Q018](questions/Q018-institutional-share-of-volume.md) | 籌碼 | 2330、2317、6488、2454 法人買賣超占成交量；買賣超 ≤ 成交量、成交均價在漲跌停內 | passing | 2026-11-27 | — |
| [Q019](questions/Q019-liquidity-ratios-internal.md) | 體質 | 2330、2317、2454、1101、6488 流動比率 > 150% 且負債比 < 50%；年度比率內部一致 | passing | 2026-11-27 | — |
| [Q020](questions/Q020-dividend-dates-two-sources.md) | 股利 | 2454、6488、3045 公告→除息→發放天數；FinMind vs yfinance 除息日與金額 | passing | 2026-11-27 | — |
| [Q021](questions/Q021-tsmc-monthly-revenue-accuracy.md) | 成長 | 2330 2026 年每月營收是否都年增；月營收與累計營收對照台積電公告 | passing | 2026-11-27 | — |
| [Q022](questions/Q022-chain-coverage-consistency.md) | 產業 | 半導體產業鏈上中下游的母體覆蓋率；產業鏈名稱 vs 基本資料簡稱 | passing | 2026-11-27 | — |
| [Q023](questions/Q023-market-cap-vs-taiex.md) | 市場 | 大盤 PBR 近 12 個月百分位；估算總市值月變動 vs 加權指數 | passing | 2026-11-27 | — |
| [Q024](questions/Q024-otc-semis-per-tpex.md) | 估值 | 上櫃半導體 5483、3105、3264 PER 百分位；TPEx 估值收盤 vs 日 K | passing | 2026-11-27 | — |
| [Q025](questions/Q025-otc-revenue-quarterly-vs-monthly.md) | 成長 | 上櫃 5483、3105、3264、6488 月營收年增率；季營收 vs 三個月月營收（otc 頁） | passing | 2026-11-27 | — |
| [Q026](questions/Q026-otc-foreign-ratio-internal.md) | 籌碼 | 上櫃 5483、3105、3264 外資持股比；TPEx qfii 股數／比率／上限內部一致 | passing | 2026-11-27 | — |
| [Q027](questions/Q027-otc-sbl-fee-pressure.md) | 籌碼 | 上櫃 5483、3105、3264 近 3 個月借券費率是否上升 | draft | — | — |
| [Q028](questions/Q028-otc-institutional-share-of-volume.md) | 籌碼 | 上櫃 5483、3105、3264 法人買賣超占成交量；TPEx 買賣超 ≤ 成交量、均價在漲跌停內 | draft | — | — |

## Issues

| ID | 嚴重度 | 維度 | 層級 | 標題 | 狀態 | 發現於 | 修復 commit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [I001](issues/I001-mops-quarterly-financials-ytd.md) | S1 | 一致性 | L1 | mops_quarterly_financials 營收與 EPS 為年初至今累計 | closed | Q002, Q003 | 指令 5 |
| [I002](issues/I002-point-in-time-look-ahead.md) | S1 | 及時性 | L2／L3 設計 | as_of 看得到尚未公布的月營收與季報 | closed（選項 B：as_of＝已公布＋`available_date`） | Q002 | 2026-11-26 |
| [I003](issues/I003-yfinance-dividend-adjusted.md) | S1 | 準確性 | L1＋L0 | dividend_yfinance 現金股利為配股還原值 | closed（2882 與 2330 2007／2008 同日配股為 L0 殘留，Q005 C1 保留 FAIL 作提醒） | Q005 | 指令 5 |
| [I004](issues/I004-normalized-as-of-from-response.md) | S2 | 完整性 | L5 | 正規化 view 的 as_of 取自回應，失敗列主鍵為 NULL | closed（第二批 8 張以 pin 重建，0 次 HTTP） | Q001 | 指令 5、2026-11-26 |
| [I005](issues/I005-otc-valuation-missing.md) | S2 | 完整性 | L1 新上游 | 上櫃股沒有估值資料 | closed（TPEx peQryDate，2007-01-02 起） | Q001 | 2026-11-26 |
| [I006](issues/I006-otc-foreign-ownership-missing.md) | S2 | 完整性 | L1 新上游 | 上櫃股沒有外資持股資料 | closed（TPEx qfii，2007-05-02 起） | Q004 | 2026-11-26 |
| [I007](issues/I007-institutional-weekend-no-fallback.md) | S2 | 完整性 | L1 | 法人買賣超取樣日遇假日不回溯 | closed | Q004 | 指令 5、2026-11-26 |
| [I008](issues/I008-financial-analysis-current-year.md) | S4 | 可取得性 | L3 | financial_analysis_year_list 產生當年度 | closed | Q003 | 指令 5 |
| [I009](issues/I009-mops-quarterly-financial-companies.md) | S2 | 完整性 | L1 | 金融股沒有季財務資料（營益分析表不含金融業） | closed | Q003（指令 4 發現） | 指令 5 |
| [I010](issues/I010-t86-failed-day-cached-as-empty.md) | S2 | 完整性 | L1 | TWSE T86 下載失敗被快取成空的交易日（擴及 TPEx、BWIBBU、MI_QFIIS、SBL、STOCK_DAY、MOPS） | closed | I007 重爬（指令 5） | 2026-11-26 |
| [I011](issues/I011-raw-and-normalized-immv-diverge.md) | S3 | 一致性 | L6 | 上游中途失敗時 raw 與正規化 IMMV 拿到不同回應 | closed（§10.5 refresh 流程） | 2026-11-25 重建 | 2026-11-26 |
| [I012](issues/I012-finmind-dividend-window-drops-event.md) | S2 | 準確性 | L1 | FinMind 股利以除息日查詢拿到上一次股利 | closed | I011 調查 | 2026-11-26 |
| [I013](issues/I013-mops-product-revenue-legit-pages-as-failure.md) | S3 | 有效性 | L1 | MOPS 產品營收把營收 0 與外國發行人免申報誤判成上游失敗 | closed | retry_failed 殘留 | 2026-11-26 |
| [I014](issues/I014-empty-answers-treated-as-failures.md) | S3 | 有效性 | L1 | yfinance 空結果、上櫃零股日、FinMind 限流殘留的誤判 | closed（seed 範圍見 I015） | 盤點剩餘不可用列 | 2026-11-26 |
| [I015](issues/I015-company-list-scope-unlisted-codes.md) | S3 | 可取得性 | L4 | company_list 含興櫃／下市代號，兩條股利歷史路徑都對它們發查 | closed（yfinance、FinMind 都改只取上市櫃） | I014 後續 | 2026-11-26 |
| [I016](issues/I016-yfinance-financials-ttm-always-null.md) | S2 | 完整性 | L0＋L3＋L1 | yfinance 季／年財報 TTM 全部 NULL（Yahoo 只留約 5 季，歷史 as_of 凍結成 found=true 空結果） | diagnosed（方案待決定） | Q007（指令 7 出題） | — |
| [I017](issues/I017-sbl-window-overlap-duplicates.md) | S2 | 唯一性 | L1／L5 | 借券相鄰取樣的 31 日視窗重疊，同一筆還券紀錄存兩次（多 302 列） | closed（L5 過濾，0 次上游呼叫） | 指令 1 剖析、Q009 | 指令 5、2026-11-27 |
| [I018](issues/I018-finmind-news-duplicates-single-day.md) | S3 | 唯一性 | L0＋L3 | finmind_news 22% 是重複 link，且每月只收取樣日當天 | diagnosed（待使用者決定） | 指令 1 剖析 | — |
| [I019](issues/I019-financial-q3-frozen-as-previous-quarter.md) | S2 | 唯一性／及時性 | L1（＋待確認 L3） | 金融股 Q3 期限當天拿不到 Q3，回上一季並凍結 | diagnosed | 指令 1 剖析、Q010 | — |
| [I020](issues/I020-2882-twse-per-eps-vs-mops.md) | S2 | 一致性 | 待確認 L0／L7 | 2882 TWSE 隱含 EPS 比 MOPS 近四季低 6–7% | diagnosed（假說待確認） | README §12.1、Q010 | — |
| [I021](issues/I021-ohlcv-current-month-frozen.md) | S3 | 及時性 | L3＋流程 | 日 K 當月那一列 insert 當天就凍結 | diagnosed（待使用者決定） | Q011 | — |
| [I022](issues/I022-product-revenue-negative-items-dropped.md) | S2 | 完整性 | L1 | MOPS 產品營收的負數項目被 adapter 丟掉，產品合計大於營收淨額（21／180 組） | diagnosed | Q008 | — |
| [I023](issues/I023-mops-revenue-page-truncated.md) | S1 | 完整性 | L0＋L1 | MOPS t21sc03 月營收頁被截斷仍當成完整頁，截斷點後的公司缺 9 月、TTM 跨 13 個月（6 列） | diagnosed（上游 18:58 已恢復，6 列待重爬） | Q008 | — |
| [I024](issues/I024-dividend-rights-issue-as-event.md) | S3（＋S4） | 有效性 | L1＋L0 | FinMind 股利把增資／減資除權（金額全 0）當成股利事件；dividend 1 列，股利歷史另有 331 個待爬 | diagnosed（方案 A／B 待決定） | Q014（指令 7 出題） | — |
| [I025](issues/I025-valuation-drops-report-period.md) | S3 | 完整性 | L1 | company_valuation 丟掉 BWIBBU 的「財報年/季」「股利年度」，PBR 換季跳動與 I020 的 EPS 基準無法驗證 | diagnosed | Q016（指令 7 出題） | — |

## 維度矩陣（pop 表 × DAMA 維度）

格子寫最近一次的斷言結果與出處（README §6.4）。`(d)` 是 draft 出題時的試跑結果；`剖析` 是只有 §7.5 剖析涵蓋；推導欄位互推不算（Q001 C3 只算範圍部分、Q005 C3 不算）。**準確一欄**：`accuracy` 斷言：Q012（EPS，passing）、Q014（現金股利，draft）；其餘只有官網抽查，是下一輪出題的第一優先。

| pop 表 | 策略 | 完整 | 唯一 | 及時 | 有效 | 準確 | 一致 | 精確 | 可取得 | 合理 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `company_valuation` | 估值 | Q001-C1 ✓；Q016-C1 ✓ | Q016-C3 ✓ | Q001-C5 ✓ | Q001-C3（範圍）✓ | — | Q001-C4 ✓；Q010-C2 ✗(d, I020) | — | Q001-C2 ✓ | — |
| `market_valuation_summary` | 市場 | Q006-C1、C2 ✓ | 剖析；Q023-C3 ✓ | Q006-C5 ✓ | Q006-C3 ✓ | — | Q006-C4 ✓（只驗量級） | — | 剖析 | Q023-C2 ✓（對加權指數） |
| `monthly_revenue_twse` | 成長 | Q002-C1 ✓ | 剖析 ✗（I023，3 組）；Q013-C3 ✓ | Q002-C3 ✓；Q013-C4 ✓ | Q021-C5 ✓ | Q021-C1、C2 ✓ | Q002-C5、C6 ✓；Q013-C2 ✓ | Q013-C5 ✓ | Q002-C2 ✓ | — |
| `monthly_revenue` | 成長 | Q013-C1 ✓ | Q013-C3 ✓ | Q013-C4 ✓ | — | Q013 抽查 ✓（非斷言） | Q013-C2 ✓（16 列） | Q013-C5 ✓ | 剖析 | — |
| `mops_quarterly_financials` | 成長 | Q012-C3 ✓；Q007-C1 ✗(d) | Q012-C4 ✓；Q010-C3 ✗(d, I019) | Q002-C4 ✓ | Q007-C5 ✓(d) | Q012-C2 ✓（8 列） | Q002-C5、Q003-C4 ✓ | Q012-C5 ✓ | 剖析 | — |
| `financial_quarterly_yfinance` | 成長 | Q007-C2 ✗(d, I016) | 剖析 | Q007-C4 ✓(d) | — | — | Q007-C3 ✓(d) | — | 剖析 | — |
| `financial_yearly_yfinance` | 體質 | Q007-C2 ✗(d, I016) | — | 剖析 | — | — | — | — | 剖析 | — |
| `financial_analysis` | 體質 | Q003-C1 ✓；Q012-C3 ✓ | 剖析 | Q019-C5 ✓ | Q003-C3 ✓；Q019-C4 ✓ | Q012-C1 ✓（9 列）；Q019 抽查 ✓ | Q003-C4 ✓；Q019-C3 ✓ | Q012-C5 ✓ | Q003-C2 ✓ | Q019-C2 ✓ |
| `raw_yearly_financials`（FinMind，無正規化表） | 體質 | — | — | — | — | — | — | — | 剖析 | — |
| `dividend` | 股利 | Q005-C2 ✓；Q020-C1 ✓；Q014-C3、C6 ✓(d) | Q020-C5 ✓；Q014-C4 ✓(d) | Q020-C3 ✓ | Q014-C5 ✗(d, I024) | Q014-C1 ✓(d，27 列) | Q005-C1 ✗（使用者決定保留，I003） | — | Q005-C4 ✓ | — |
| `dividend_yfinance` | 股利 | Q014-C3 ✓(d) | Q014-C4 ✓(d) | 剖析 | — | Q014-C2 ✓(d，27 列) | Q005-C1 ✗（同上） | — | 剖析 | — |
| `institutional_net_buy_sell` | 籌碼 | Q004-C1 ✓；Q015-C1 ✓ | 剖析；Q015-C4 ✓ | Q015-C5 ✓ | Q018-C4 ✓ | — | Q004-C2 ✓ | Q015-C6 ✓ | 剖析 | Q018-C2 ✓ |
| `foreign_ownership` | 籌碼 | Q004-C3 ✓ | 剖析；Q015-C4 ✓ | Q004-C4 ✓；Q015-C5 ✓ | Q004-C4 ✓ | — | Q015-C2、C3 ✓ | Q015-C6 ✓ | 剖析 | — |
| `sbl_history` | 籌碼 | Q009-C3 ✓ | Q009-C1 ✓ | 剖析 | Q009-C5 ✓ | Q009 抽查 ✓（非斷言） | Q009-C2 ✓ | — | Q009-C4 ✓ | Q009-C6 ✓ |
| `ohlcv_daily` | 技術 | Q011-C1 ✓ | Q011-C2 ✓ | Q011-C6 ✓（重爬當月後） | Q018-C4 ✓ | Q011 抽查 ✓（非斷言） | Q001-C4 ✓；Q011-C5 ✓；Q018-C3 ✓ | Q011-C3 ✓ | 剖析 | Q011-C4 ✓ |
| `chain_info_list` | 產業 | Q017-C1 ✓；Q022-C1 ✓ | Q017-C3 ✓；Q022-C3 ✓ | — | Q022-C4 ✓ | — | Q022-C2 ✓（872 家） | — | Q008-C4 ✓ | — |
| `company_basic_info_list` | 產業 | Q017-C1 ✓ | — | — | Q017-C4 ✓ | Q017 抽查 ✓（非斷言） | Q017-C2 ✓（5 列） | — | 依賴鏈 | Q017-C5 ✓ |
| `company_business_items` | 產業 | 剖析 | 剖析（上游重複，合法） | — | — | — | — | — | Q008-C4 ✓ | — |
| `product_revenue` | 產業 | 剖析 | Q008-C5 ✓ | 剖析 | Q008-C2 ✓ | Q008 抽查 ✓（非斷言） | Q008-C1 ✗（I022）；C3 ✓ | — | 剖析 | — |
| `finmind_news` | 事件 | 剖析 | 剖析 ✗（I018） | 剖析 | — | — | — | — | 剖析 | — |

## 剖析基線（§7.5，2026-11-27 建立）

全空欄位與實體重複全部列出；部分空值同一張表、同一原因的欄位合併成一行。

| pop 表 | 欄位或實體鍵 | 現況 | 分類 | 依據 |
| --- | --- | --- | --- | --- |
| `financial_quarterly_yfinance`、`financial_yearly_yfinance` | `eps_ttm`、`net_income_ttm`、`operating_margin_pct`、`revenue_ttm` | 0% 非空（8 欄） | 缺陷 I016 | Yahoo 只留約 5 季，歷史 as_of 凍結成 found=true 空結果 |
| `financial_quarterly_yfinance` | `latest_quarter_date`、`latest_quarter_eps`（48.9%）、`latest_quarter_net_income`（5.3%） | 部分空值 | 缺陷 I016 | 同上 |
| `financial_yearly_yfinance` | `latest_quarter_*`（99.7%） | 部分空值 | 合法 | 2 檔 Yahoo 未收錄（I014） |
| `company_valuation`、`financial_analysis` | `reason` | 0% 非空 | 結構性 | 只有 found=false 才有值（`app/valuation_source.py` 551 行），正規化表的列都是成功的回應 |
| `dividend_yfinance` | `cash_payment_date`、`stock_ex_dividend_date`、`announcement_date` | 0% 非空 | 結構性 | 共用轉換 `_dividend_row_to_event`（`app/service.py`）讀 FinMind 的鍵名，yfinance 的列沒有這三個欄位 |
| `company_valuation` | `per`、`eps_ttm`（90.6%） | 部分空值 | 合法 | 虧損期間 TWSE 不給 PER（Q001 已知限制） |
| `dividend` | `cash_ex_dividend_date`（98.3%）、`cash_payment_date`（86.7%）、`stock_ex_dividend_date`（6.7%） | 部分空值 | 合法 | 只有股票股利的年份沒有現金除息日（rule 17 的例子）；多數年份沒有股票股利 |
| `financial_analysis` | 全部比率欄位（90.6–94.8%） | 部分空值 | 合法 | 15 列該年度沒有年報（404）；週轉率類 92% 是金融業沒有存貨／應收（I009 同類） |
| `foreign_ownership` | `china_investment_limit_ratio_pct`（37.5%） | 部分空值 | 結構性 | TPEx 來源沒有這欄（`app/foreign_ownership_source.py` 221 行） |
| `foreign_ownership` | `change_reason`、`last_change_date`（83.0%） | 部分空值 | 合法 | 沒有異動紀錄的股票 |
| `institutional_net_buy_sell` | `foreign_dealers_net_buy_sell`（45.6%） | 部分空值 | 結構性 | 2018-01 以前的取樣沒有外資自營欄（Q004 C2） |
| `institutional_net_buy_sell` | `dealers_proprietary_*`、`dealers_hedge_*`（68.7%） | 部分空值 | 結構性 | 2014-12 以前沒有自營商自行／避險拆分 |
| `institutional_net_buy_sell` | `trade_date` 等 9 欄（94.9%，27 列） | 部分空值 | **待查** | 8 檔小型股 2012–2017 的取樣，raw `found=true` 但沒有該股的列；可能是當天無法人交易，也可能是 I010 殘留 |
| `monthly_revenue_twse` | `latest_month_yoy_pct`（85.1%）、`ttm_value`（86.4%）、`ttm_yoy_pct`（71.5%） | 部分空值 | 合法為主，部分**待查** | 一半落在各股窗口起點後 12／24 個月內（沒有前期可比）；其餘 36 列 YoY、69 列 TTM YoY 待查 |
| `mops_quarterly_financials` | `revenue_millions`、毛利率等（96.3%）；其他欄位 99.7% | 部分空值 | 合法 | 金融業沒有這些科目（I009）；1 列是 8463 上市前 |
| `ohlcv_daily` | `open`／`high`／`low`／`close`（99.9%） | 部分空值 | 合法 | 無成交日 |
| `ohlcv_daily` | `change`（99.5%） | 部分空值 | 結構性 | 除權息日 TWSE 標 `X`，解析成 NULL（README §7.1） |
| `sbl_history` | `completion_close_price`（92.1%） | 部分空值 | 結構性 | 全部是 2005–2012 的紀錄，早期 t13sa870 沒有這欄 |
| `finmind_news` | `(stk_code, link)` | 321 組、多 431 列 | 缺陷 I018 | 上游同一份回應就重複 |
| `sbl_history` | 7 欄實體鍵，跨 as_of | 0（2026-11-27 前 290 組、多 302 列） | 已修 I017 | 31 日視窗重疊；正規化 SQL 只留 `(as_of − 1 個月, as_of]` |
| `sbl_history` | 同上，同一 as_of 內 | 325 組、多 445 列 | 合法（上游如此） | 上游沒有交易序號，條件相同的多筆合約無法分辨 |
| `company_business_items` | `(stock_id, code)` | 261 組、多 263 列 | 合法（上游如此） | seed 沒有重複代號，重複來自經濟部清單；用到時去重 |
| `mops_quarterly_financials` | `(stk_code, fiscal_year, quarter)` | 1 組、多 1 列 | 缺陷 I019 | 2882 2026Q2 在 Q3 期限被凍結 |
| `monthly_revenue_twse` | `(stk_code, latest_month_label)` | 3 組、多 3 列（2026-11-27 第 1 輪起） | 缺陷 I023 | MOPS 9 月彙總頁被截斷，as_of 2026-10-10 回 8 月 |

## 品質趨勢（每次盤點一行）

| 日期 | raw 平均 usable%（可取得） | 最低三個 raw（usable%） | 全空欄位數（完整） | 實體重複列數（唯一） | 新開／關閉 issue |
| --- | --- | --- | --- | --- | --- |
| 2026-11-25 | 86.1（23 個 json 欄位） | `raw_monthly_revenue` 0.0（僅 1 列）、`raw_sbl_history` 40.8、`raw_finmind_news` 72.6 | — | — | 指令 3 新開 I001–I008 |
| 2026-11-25（指令 6 回歸） | 85.9 | `raw_monthly_revenue` 0.0、`raw_sbl_history` 46.5、`raw_finmind_news` 72.6 | — | — | 指令 4 開 I009、指令 5 開 I010；關閉 I001、I008、I009。回歸：passing 的 Q003 4／4 PASS。平均略降是因補爬新增了 I005 的上櫃估值 NULL 列 |
| 2026-11-26（重建＋重試） | 90.9 | `raw_monthly_revenue` 0.0（僅 1 列 FinMind seed）、`raw_finmind_news` 72.6（26 列為 404，無新聞）、`raw_sbl_history` 77.0（85 列為 404） | — | — | 開 I011、I012；關閉 I002、I003、I005、I006、I007、I010、I011、I012。Q001–Q004 全 PASS，Q005 只剩刻意保留的 C1 |
| 2026-11-26（I004、I013、I014 後） | 95.4 | `raw_finmind_news` 72.6（404，無新聞）、`raw_sbl_history` 77.0（404，無借券）、`raw_company_info` 82.3（187 檔興櫃／下市代號） | — | — | 開並關閉 I013、I014；I004 關閉。剩下的不可用列都是合法無資料 |
| 2026-11-26（I015 後） | 96.0 | `raw_finmind_news` 72.6（404，無新聞）、`raw_sbl_history` 77.0（404，無借券）、`raw_company_info` 82.3（187 檔興櫃／下市代號，判定上市櫃的那一步，保留） | — | — | 開並關閉 I015：`raw_dividend_history_yfinance` 85.4 → 100（seed 改 `company_basic_info_list`）。FinMind 那條路依使用者決定也只留上市櫃（514 → 423 列，移除 70 家非上市櫃公司的股利歷史） |
| 2026-11-26（指令 7） | 96.0 | `raw_finmind_news` 72.6、`raw_sbl_history` 77.0、`raw_company_info` 82.3（皆為已知的合法無資料） | — | — | 執行 Q005（補爬 FinMind 9 個事件，C1 新增 1 筆 2882 L0 違規，非退化）；出 Q006–Q008；開 I016（S2，diagnosed）。注意：`raw_*_financials_yfinance` 的 100% 是假象（found=true 空結果，I016） |
| 2026-11-27（指令 7，第一次維度剖析） | 96.0 | `raw_finmind_news` 72.6、`raw_sbl_history` 77.0、`raw_company_info` 82.3（皆為已知的合法無資料） | 13（8 個 I016、5 個結構性） | 997（缺陷：`finmind_news` 431、`sbl_history` 跨 as_of 302、`mops_quarterly_financials` 1；合法：`company_business_items` 263。另有借券同 as_of 445 列未計入，列為合法） | 執行 Q006（passing）；出 Q009–Q011；開 I017–I021（全部 diagnosed） |
| 2026-11-27（第 1 輪指令 7） | 96.0 | `raw_finmind_news` 72.6、`raw_sbl_history` 77.0、`raw_company_info` 82.3（皆為已知的合法無資料） | 13（不變） | 698（`sbl_history` 跨 as_of 302 → 0，I017 修好；本輪補爬後 `monthly_revenue_twse` 新增 3 組，下一輪剖析會報） | 執行 Q008（failing）；開 I022（S2）、I023（S1），都已 diagnosed |
| 2026-11-27（第 2 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（新增 `monthly_revenue_twse` 3 組，I023） | 執行 Q009（passing）；沒有新 issue |
| 2026-11-27（第 3 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q011（passing）；出 Q014；開 I024（S3＋S4） |
| 2026-11-27（第 4 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q012（passing，第一個 accuracy 通過）；出 Q015；沒有新 issue |
| 2026-11-27（第 5 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q015（passing）；出 Q016；開 I025（S3） |
| 2026-11-27（第 6 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q016（passing）；出 Q017；I023 上游恢復 |
| 2026-11-27（第 7 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q013（passing）；出 Q018；沒有新 issue |
| 2026-11-27（第 8 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q017（passing）；出 Q019；沒有新 issue |
| 2026-11-27（第 9 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q018（passing）；回歸 12 題全過；出 Q020；沒有新 issue |
| 2026-11-27（第 10 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q019（passing）；出 Q021；沒有新 issue |
| 2026-11-27（第 11 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q020（passing，決策情境 L7 改寫）；出 Q022；沒有新 issue |
| 2026-11-27（第 12 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q021（passing）；出 Q023；沒有新 issue |
| 2026-11-27（第 13 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q022（passing）；出 Q024；沒有新 issue |
| 2026-11-27（第 14 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q023（passing）；出 Q025；沒有新 issue |
| 2026-11-27（第 15 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q024（passing）；出 Q026；沒有新 issue |
| 2026-11-27（第 16 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q025（passing）；出 Q027；沒有新 issue |
| 2026-11-27（第 17 輪指令 7） | 96.0 | 同上（皆為已知的合法無資料） | 13（不變） | 701（不變） | 執行 Q026（passing）；出 Q028；沒有新 issue |

## 最新盤點發現（2026-11-27 20:20 第 17 輪指令 7，`snapshots/2026-11-27.json`）

- **和第 16 輪比**：列數變化全部來自 Q025 補爬（月營收 +39、季報 +12）。
- **第 16 輪提交**：`ceec411`，PR #82 8 個 check 全過，沒有補救。
- **挑題**：執行 Q026（最舊的可跑 draft），補爬 TPEx 72 列；6／6 PASS，TPEx qfii 的比率也符合捨去規則。3105 外資持股 4 個月 26.6% → 45.5%：發行股數不變、增持約占同期成交量 10%，合理。
- **出題（指令 2）**：補 1 題 **Q028**（上櫃量價）：Q018 換成上櫃，不需補爬；試跑 4／4 PASS（均價 716 天、買賣超 ≤ 成交量 36 次）。
- **建議修復順序**：同第 6 輪。

## 前次盤點發現（2026-11-27 20:15 第 16 輪指令 7，`snapshots/2026-11-27.json`）

- **和第 15 輪比**：列數變化全部來自 Q024 補爬（估值 +36、日 K +720）。
- **第 15 輪提交**：`270af87`，PR #82 第一次 `test-live-sql` 失敗：`poc.company_valuation` 45 秒逾時（0 bytes），同 log 有 `/quarterly-financials` 504，和第 2 輪同一種上游環境失敗（本 commit 只改 `verify_questions/`）。`gh run rerun 45994768248 --failed` 後 8 個全過。README 指令 8 把這個模式列進「上游環境造成的」例子。
- **挑題**：執行 Q025（最舊的可跑 draft）。補爬前確認 MOPS `otc/t21sc03` 頁完整；補爬 51 列，5／5 PASS（季營收 vs 月營收 16 季一致、沒有重複營收月）。3105、3264 成長動能強，3264 營收抽查一致。
- **出題（指令 2）**：補 1 題 **Q027**（上櫃借券）：Q009 換成 5483、3105、3264。上櫃股可能有月份沒有還券（合法 404），C4 違規時先查。
- **建議修復順序**：同第 6 輪。

## 重爬紀錄（crawl_delete_rows → crawl_insert_filtered）

| 日期 | seed | filters | 刪除列數 | pinned | 重爬後狀態 | 相關 issue |
| --- | --- | --- | ---: | ---: | --- | --- |
| 2026-11-25 | `sbl_history_monthly_list` | `{stk_code: "2330", month_start_date: "2015-08-05"}` | 1 | 1 | 原本 NULL（404）→ 可用，49 筆借券紀錄 | 指令 0 實測 |
| 2026-11-25 | `mops_quarterly_financials_quarter_list` | 16 檔全部（依 http_log 還原 356 組） | 355 | 355 | 單季值；NULL 12 → 1（8463 上市前） | I001、I009 |
| 2026-11-25 | `financial_analysis_year_list` | `{year: 2026}` | 21 | 21 | 不再產生（seed 終點修正） | I008 |
| 2026-11-25 | `dividend_event_yfinance_list` | 126 檔、最後一次 split 之前的事件 | 163 | 163 | 還原成實際配發金額 | I003 |
| 2026-11-25 | `institutional_monthly_list` | `row` 為空的 448 列 | 448 | 448 | **中斷**：288 列重新 insert（多數仍空）、160 列待爬；TWSE 開始封鎖 | I007、I010 |
| 2026-11-26 | 全部 seed（重建） | §10.6：`create_views()` → `create_mat_views(recreate=True)`，再依備份還原 seed key | — | — | 零漂移；TWSE 中途 307 封鎖造成的 NULL 交給 retry_failed | I002、I005、I006 |
| 2026-11-26 | 13 個 raw 的 seed | `retry_failed.py --apply`（NULL 或 `source_errors`；OHLCV 加 `--include-not-found`） | ≈1,240 | 同左 | OHLCV 空月 454 → 0、月營收 111 → 0、FinMind 年報／股利歷史全數可用；被 rollback 的分歧列見 I011 | I007、I010、I011 |
| 2026-11-26 | `dividend_event_list` | `refresh_immv` raw_dividend → dividend | 51 | — | 可用 33 → 51，reference_date＝as_of 51／51 | I011、I012 |
| 2026-11-26 | `institutional_monthly_list` | `refresh_immv` raw → 正規化 | 533 | — | 失敗 11 → 0 | I011 |
| 2026-11-26 | 8 張正規化 IMMV 的 seed | `pin_seed_responses` → `update_mat_views` → `unpin_responses` | — | 3,586 | 列數不變、NULL 主鍵 0、上游呼叫 0 | I004 |
| 2026-11-26 | `product_revenue_filer_list` | `retry_failed.py raw_product_revenue --apply --include-not-found` | 2 | 2 | 5206 營收 0 → found；2637 → 免申報（合法無資料） | I013 |
| 2026-11-26 | `company_list` | `retry_failed.py raw_dividend_history_yfinance --apply`（4587 逾時後另以 reinsert 補回） | 184 | 184 | 上市櫃代號全部可用；剩 154 列為興櫃／下市代號 | I014 |
| 2026-11-26 | `financial_year_yfinance_list`、`company_valuation_monthly_list`、`financial_month_list` | `retry_failed.py --apply` | 6 | 6 | 6913／7772 → Yahoo 未收錄（found=false）；1264 回溯到有收盤價的交易日；FinMind 月營收可用 | I014 |
| 2026-11-26 | `company_basic_info_list`（`raw_dividend_history_yfinance` 換 seed） | `pin_seed_responses` → `update_mat_views` → `unpin_responses` | 187（非上市櫃，全為空） | 870 | 870 列全可用、有事件的列 834 不變、上游呼叫 0 | I015 |
| 2026-11-26 | `company_finmind_list` | `update_mat_views`（replace_hidden_view）→ `delete_seed_rows {stk_code: 91 個非上市櫃代號}` | 91 | 91 | `raw_dividend_history` 514 → 423、`dividend_event_list` 不變、上游呼叫 0 | I015 |
| 2026-11-27 | `sbl_history_monthly_list`（只改正規化 SQL） | `pin_seed_responses` → `update_mat_views(['sbl_history'])` → `unpin_responses` | 296（正規化列） | 370 | 列數 27,058 → 26,762、跨 as_of 重複 0、上游呼叫 0 | I017 |
| 2026-11-27 | `ohlcv_monthly_list` | `{stk_code: [2330, 2317, 6488], month_start_date: 2026-11-01}`（§8.3 規則 5） | 3 | 3 | `ohlcv_daily` 每檔 11 月 18 → 20 個交易日，最新 2026-11-27；3 次 API 請求 | I021（Q011） |
