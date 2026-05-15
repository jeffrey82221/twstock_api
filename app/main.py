"""FastAPI 入口。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import icchain
from .service import query, search_companies

app = FastAPI(
    title="台灣上市櫃公司查詢 API",
    description=(
        "整合 TWSE / TPEx OpenAPI 與 FinMind 公開資料，"
        "查詢任一上市/上櫃公司的基本資料、EPS、營收、淨利、股利、營業利潤率、營收成長率等。"
        "支援 as_of 任一日期回推 TTM/年化值。"
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
async def _on_startup() -> None:
    # 啟動時依磁碟快取快速還原；若無快取則背景抓取。
    await icchain.ensure_loaded(background=True)


@app.get("/api/health")
async def health():
    return {"ok": True, "icchain": icchain.status()}


@app.get("/api/chains")
async def api_list_chains():
    """回傳全部產業鏈類別清單與載入狀態。"""
    return {"chains": icchain.list_chains(), "status": icchain.status()}


@app.get("/api/chain/{ic_code}")
async def api_chain(ic_code: str):
    """取得某個產業鏈的完整上中下游樹狀資料。"""
    await icchain.ensure_loaded(background=True)
    if not icchain.is_loaded():
        raise HTTPException(status_code=503, detail="產業價值鏈資料載入中，請稍後重試")
    chain = icchain.get_chain(ic_code)
    if not chain:
        raise HTTPException(status_code=404, detail=f"查無產業鏈 {ic_code}")
    return chain


@app.get("/api/search")
async def api_search(q: str = Query(..., description="關鍵字：股票代號或公司名稱"), limit: int = 20):
    return {"results": await search_companies(q, limit=limit)}


@app.get("/api/company/{stock_id}")
async def api_company(
    stock_id: str,
    as_of: str | None = Query(
        None,
        description="查詢基準日 YYYY-MM-DD；省略則用今天。回傳的 TTM/月營收皆以此日往前回推。",
    ),
):
    result = await query(stock_id, as_of)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result.get("error", "查無資料"))
    return result


# 靜態前端：直接把 static/ 挂在根路徑，讓 ./style.css ./app.js 可直接讀取
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/style.css")
    async def _style():
        return FileResponse(str(STATIC_DIR / "style.css"))

    @app.get("/app.js")
    async def _appjs():
        return FileResponse(str(STATIC_DIR / "app.js"))
