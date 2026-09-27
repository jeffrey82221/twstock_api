# app/ — TWStock Query FastAPI 服務

本目錄是 TWStock Query 平台的資料抓取與查詢 API 本體，使用 FastAPI 提供 REST endpoint，
整合免費公開資料源（TWSE OpenAPI、TPEx OpenAPI、FinMind v4、MOPS、經濟部商工 API、yfinance），
供任一上市/上櫃公司查詢基本資料、財報、營收、股利、法人買賣超、估值等資訊。

啟動方式：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 5002
```

## 目錄結構

| 檔案 | 說明 |
| --- | --- |
| `main.py` | FastAPI 入口，定義所有 `/api/*` route，組裝各資料源並回傳 Pydantic response model。 |
| `schemas.py` | Pydantic 回應模型，每個欄位皆附 description 說明資料源、來源 API URL 與計算邏輯。 |
| `service.py` | 組裝查詢服務，串接多個資料源並整合成單一回應。 |
| `sources.py` | 免費公開資料來源 client（低階 HTTP 呼叫封裝）。 |
| `icchain.py` | 櫃買中心「產業價值鏈資訊平台」資料讀取與索引。 |
| `industry.py` | TWSE / TPEx 產業別代碼對照。 |
| `ohlcv_source.py` | OHLCV 歷史行情資料源（TWSE + TPEx 融合）。 |
| `institutional_source.py` | 三大法人買賣超日報資料源（TWSE T86 + TPEx dailyTrade 融合）。 |
| `foreign_ownership_source.py` | TWSE MI_QFIIS 外資及陸資投資持股統計資料源。 |
| `twse_sbl_source.py` | TWSE 借券還券完成情形（SBL t13sa870）資料源。 |
| `valuation_source.py` | 全市場本益比 / 殖利率 / 股價淨值比彙總資料源（TWSE BWIBBU_d）。 |
| `mops_quarterly_financials.py` | MOPS 季報營益分析與綜合損益表資料源（IFRS quarterly）。 |
| `mops_financial_analysis.py` | MOPS 財務分析彙整表資料源（`t51sb02`）。 |
| `yfinance_source.py` | yfinance 來源，取得台股季財報（EPS / 淨利 / 營業利潤率等衍生用）。 |
| `finmind_news_source.py` | FinMind TaiwanStockNews 個股新聞資料源。 |

## 主要 API Endpoint

- `GET /api/health` — 健康檢查
- `GET /api/chains`、`GET /api/chain/{ic_code}`、`POST /api/chain/refresh` — 產業價值鏈
- `GET /api/search` — 公司搜尋
- `GET /api/company/{stock_id}`、`/basic`、`/business-items` — 公司基本資料
- `GET /api/company/{stock_id}/financials`、`/financials/yfinance` — 財報
- `GET /api/company/{stock_id}/revenue`、`/revenue/twse` — 月營收
- `GET /api/company/{stock_id}/dividend`、`/dividend/yfinance`、`/dividend/history`、`/dividend/history/yfinance` — 股利
- `GET /api/product-revenue/filers`、`/api/company/{stock_id}/product-revenue` — 產品營收
- `GET /api/company/{stock_id}/value-chain` — 個股所屬產業鏈
- `GET /api/ohlcv` — 歷史行情
- `GET /api/institutional-net-buy-sell` — 三大法人買賣超
- `GET /api/company/{stock_id}/sbl-history` — 借券還券
- `GET /api/company/{stock_id}/foreign-ownership` — 外資持股
- `GET /api/market-valuation-summary`、`/api/company-valuation` — 市場與個股估值
- `GET /api/v1/fundamentals/financial-analysis` — MOPS 財務分析年報
- `GET /api/company/{stock_id}/quarterly-financials` — MOPS 季報
- `GET /api/company/{stock_id}/news/finmind` — 個股新聞

完整參數與回應格式請見 FastAPI 自動產生的 Swagger 文件（`/docs`）或 `schemas.py` 中各 response model 的欄位說明。

---

_檢核與描述整理：[Perplexity Computer](https://perplexity.ai/computer)_
