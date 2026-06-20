# TWStock Query · 台灣上市櫃公司查詢平台

> **Version: v0.0.3**

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

## 安裝與啟動

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

開啟 http://localhost:5000

## API

- `GET /api/health` 健康檢查
- `GET /api/search?q=2330&limit=20` 模糊搜尋公司
- `GET /api/company/{stock_id}?as_of=YYYY-MM-DD` 查詢公司資訊
  - `as_of` 可省略，預設今天
  - 若是過去日期，所有 TTM/月營收/股利會用該日「已公告」的最後資料
  - 回應內含 `value_chain.memberships`（公司在哪些產業鏈、上中下游、子分類）與 `value_chain.neighbors_by_chain`（同鏈上下游鄰居公司）
- `GET /api/chains` 列出全部 47 條產業鏈（IC 代碼 + 名稱）
- `GET /api/chain/{ic_code}` 取得單一產業鏈完整結構（上/中/下游 → 子鏈 → 公司清單）

Swagger UI: http://localhost:5000/docs

## 資料來源限制

- **TWSE/TPEx OpenAPI**：免費公開，每日刷新一次基本資料（資本額、總經理可能落後 1 天）
- **FinMind v4**：免費 300 req/hr，無需 token；本服務內建 1 小時 TTL 快取
- **經濟部商工 API**：全國公司登記資料集（`236EE382-...025E7C`），含「所營事業 Cmp_Business」欄位。本服務依「營利事業統一編號 Business_Accounting_NO」查詢，內建 24 小時快取。商工 API 資料集每日更新，公司所營事業變動頻率低（通常最多一年一次）；同一公司可能同時存在「行業分類」與「敘述條目」，後者最具識別度。
- 季財報：依公開資訊觀測站申報日為準（一般 Q1 5 月、Q2 8 月、Q3 11 月、Q4 隔年 3 月）
- 月營收：每月 10 日前公告上月數據
- **櫃買中心 產業價值鏈資訊平台**（`ic.tpex.org.tw`）：47 條產業鏈，server-rendered HTML，無 API。本服務首次查詢時 lazy 背景全量收集（47 頁併發，semaphore=6，~8 秒），落盤至 `data/icchain.json`，TTL 7 天。公司比對採純 `stk_code` 反查（嚴謹，不做模糊比對），約 1853 家上市櫃公司有產業鏈定位資料。

## 結構

```
twstock_api/
├── app/
│   ├── main.py        # FastAPI 入口、路由
│   ├── service.py     # 整合邏輯、TTM 計算
│   ├── sources.py     # TWSE/TPEx/FinMind/商工 client 與快取
│   ├── industry.py    # 產業別代碼對照
│   └── icchain.py     # 櫃買中心產業價值鏈解析、索引、快取
├── static/            # 前端（純 HTML/CSS/JS）
├── requirements.txt
└── README.md
```
