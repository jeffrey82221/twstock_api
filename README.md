# TWStock Query · 台灣上市櫃公司查詢平台

> **Version: v0.0.5**

整合免費公開資料源（TWSE OpenAPI、TPEx OpenAPI、FinMind v4、經濟部商工 API），
提供任一上市/上櫃公司的基本資料、主要營業項目、EPS、營收、淨利、股利、營業利潤率、營收成長率、總經理等資訊。

支援 `as_of` 任一日期回推 TTM（trailing twelve months）/ 年化值。

## 欄位來源對照

| 欄位 | 來源 |
| --- | --- |
| 公司統編 | TWSE/TPEx 基本資料 |
| 公司名稱 | TWSE/TPEx 基本資料 |
| 公司資本額 | TWSE/TPEx 基本資料（實收資本額） |
| 股票代號 | TWSE/TPEx 基本資料 |
| 產業別 | TWSE/TPEx 產業別代碼解碼（半導體業、金融保險業…） |
| 主要營業項目 | 經濟部商工 API`公司登記基本資料`中的「所營事業（Cmp_Business）」。區分為公司自行描述之「敘述條目」（最具識別度，例如台積電「依客戶之訂單製造與銷售積體電路以及其他晶圓半導體裝置」），與「行業分類」（中華民國行業標準分類代碼） |
| EPS | FinMind `TaiwanStockFinancialStatements` `type=EPS`，TTM = 最近 4 季加總 |
| 營收 | FinMind `TaiwanStockMonthRevenue`，最近 12 個月加總（TTM） |
| 淨利 | FinMind FinancialStatements `IncomeAfterTaxes`，TTM |
| 股利股息 | FinMind `TaiwanStockDividend`，取「除息日 ≤ as_of」最後一次 |
| 營業利潤率 | OperatingIncome / Revenue（皆 TTM） |
| 營收成長率 | 月營收 YoY；TTM YoY（最近 12 月 vs 前 12 月） |
| 總經理 | TWSE/TPEx 基本資料 |
| 產業價值鏈定位 | 櫃買中心 產業價值鏈資訊平台（上/中/下游、子鏈分類、上下游公司） |
| 主要產品比重 | 公開資訊觀測站（MOPS）`ajax_t05st08_all` |

## 安裝與啟動

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

開啟 http://localhost:5000

## API

- `GET /api/health` 健康檢查
- `GET /api/search?q=2330&limit=20` 模糊搜尋公司
- `GET /api/company/{stock_id}?as_of=YYYY-MM-DD` 查詢公司資訊（聚合 endpoint）
  - `as_of` 可省略，預設今天
  - 若是過去日期，所有 TTM/月營收/股利會用該日「已公告」的最後資料
  - 回應內含 `value_chain.memberships`（公司在哪些產業鏈、上中下游、子分類）與 `value_chain.neighbors_by_chain`（同鏈上下游鄰居公司）
- `GET /api/company/{stock_id}/basic` 公司基本資料
- `GET /api/company/{stock_id}/business-items` 主要營業項目
- `GET /api/company/{stock_id}/financials?as_of=YYYY-MM-DD` 財務指標（EPS、淨利、營業利潤率）
- `GET /api/company/{stock_id}/revenue?as_of=YYYY-MM-DD` 月營收與 TTM / YoY
- `GET /api/company/{stock_id}/dividend?as_of=YYYY-MM-DD` 股利
- `GET /api/company/{stock_id}/value-chain` 公司在產業鏈的定位與鄰居
- `GET /api/company/{stock_id}/product-revenue?as_of=YYYY-MM-DD` 主要產品比重（MOPS）
- `GET /api/chains` 列出全部 47 條產業鏈（IC 代碼 + 名稱）
- `GET /api/chain/{ic_code}` 取得單一產業鏈完整結構（上/中/下游/子鏈 → 公司清單）

Swagger UI: http://localhost:5000/docs

## 資料來源限制

- **TWSE/TPEx OpenAPI**：免費公開，每日刷新一次基本資料（資本額、總經理可能落後 1 天）
- **FinMind v4**：免費 300 req/hr，無需 token；本服務內建 1 小時 TTL 快取
- **經濟部商工 API**：全國公司登記資料集（`236EE382-...025E7C`），含「所營事業 Cmp_Business」欄位。本服務依「營利事業統一編號 Business_Accounting_NO」查詢，內建 24 小時快取。商工 API 資料集每日更新，公司所營事業變動頻率低（通常最多一年一次）；同一公司可能同時存在「行業分類」與「敘述條目」，後者最具識別度。
- 季財報：依公開資訊觀測站申報日為準（一般 Q1 5 月、Q2 8 月、Q3 11 月、Q4 隔年 3 月）
- 月營收：每月 10 日前公告上月數據
- **櫃買中心 產業價值鏈資訊平台**（`ic.tpex.org.tw`）：47 條產業鏈，server-rendered HTML，無 API。本服務首次查詢時 lazy 背景全量收集（47 頁併發，semaphore=6，~8 秒），落盤至 `data/icchain.json`，TTL 7 天。公司比對採純 `stk_code` 反查（嚴謹，不做模糊比對），約 1853 家上市櫃公司有產業鏈定位資料。
- **公開資訊觀測站（MOPS）主要產品比重**：`ajax_t05st08_all` 月度申報資料，依 `as_of` 自動回溯最近一份有效申報期。

## 結構

```
twstock_api/
├── app/
│   ├── main.py        # FastAPI 入口、路由
│   ├── service.py     # 整合邏輯、TTM 計算、來源錯誤聚合
│   ├── sources.py     # TWSE/TPEx/FinMind/商工/MOPS client 與快取、SourceError 追蹤
│   ├── industry.py    # 產業別代碼對照
│   ├── icchain.py     # 櫃買中心產業價值鏈解析、索引、快取
│   └── schemas.py     # Pydantic 回應模型
├── static/            # 前端（純 HTML/CSS/JS，含 as_of 日期選擇器）
├── db/                # PostgreSQL schema / view / 同步腳本（從 FastAPI 延伸）
├── requirements.txt
└── README.md
```

## 版本紀錄

### v0.0.5 — 2026-06-20

**Milestone：驗證 Perplexity Computer 可作為軟體工程師執行 Github PR 流程**

- 版本號 v0.0.4 → **v0.0.5**。
- 驗證 Perplexity Computer 可獨立完成「建立 feature branch → 編輯檔案 → commit → push → 用 GitHub API 發 PR」全流程。
- 透過安全憑證 vault 以 `jeff-perplexity-bot` 名義操作，PAT 不再經由對話傳遞。

### v0.0.4 — 2026-06-20

**Milestone：與 POC 版本一致性確認 + Github PR-based 開發流程啟用**

- 確認 GitHub repo `app/`、`static/`、`requirements.txt` 與 Perplexity Computer POC 版本逐檔一致（除 `__pycache__` 外無差異）。
- 本機 `uvicorn app.main:app --host 0.0.0.0 --port 5000` 啟動成功，煙霧測試通過：
  - `/api/health` 回報 icchain 已載入 47 條鏈、2199 家公司
  - `/api/search?q=2330` 模糊搜尋正常
  - `/api/company/2330/basic` 取得 TWSE/TPEx 基本資料正常
  - `/api/company/2330?as_of=2024-12-31` 聚合查詢 + 日期回溯正常
  - `/api/company/2330/product-revenue` MOPS 產品比重正常
  - `/api/chain/D000` 產業鏈查詢正常
- 啟用 **Github PR-based 開發流程**：往後對「台灣上市櫃公司查詢平台」的所有異動皆走 feature branch + Pull Request 流程，方便逐次審查 diff。
- README 加入版本紀錄章節與完整 endpoint 列表（補上 per-source endpoint 與 product-revenue endpoint）。

### v0.0.3 — 之前版本

- 加入 `db/` 資料夾：PostgreSQL 同步 schema、views（pg_ivm）、populated plan、原始層 raw 表設計。
- 加入 schema creation、financial statement、pg_ivm extension。
- 強化 not found 穩定性與 log。

### v0.0.2 — 之前版本

- 來源錯誤追蹤機制：每個 endpoint 在源頭（TWSE/TPEx/FinMind/GCIS/MOPS）遭反爬/限流時，回應自動帶 `source_errors` 並將 `found=False`；FastAPI 也會 log 出 status code 與錯誤訊息。
- 主要產品比重支援 `as_of` 日期回溯查詢（MOPS `ajax_t05st08_all` 逐月回溯最近申報期）。
- 產業鏈 segments 改為彈性結構（streams list），不再硬編「上/中/下游」三段。

### v0.0.1 — 初版

- TWSE / TPEx / FinMind / 經濟部商工 API 整合。
- 6 個 per-source endpoint + 1 個聚合 endpoint。
- 櫃買中心產業價值鏈 47 鏈背景擷取與快取。
- 前端（純 HTML/CSS/JS）查詢介面。
