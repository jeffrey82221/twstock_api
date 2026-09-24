"""資料庫 DEMO — poc/pop schema 依賴關聯圖 + 資料量監控 + pipeline.py 操作面板。

獨立於 ``app/main.py``（主要業務 API）之外的另一個 FastAPI app，啟動方式：

    uvicorn db_demo.main:app --host 0.0.0.0 --port 5100

功能：
1. ``GET /api/dag``            poc schema 的 view 依賴關聯圖（airflow 風格節點/邊）
2. ``GET /api/view/{name}/columns``  單一 poc view 的欄位內容
3. ``GET /api/pop/row_counts``  pop schema 各表目前列數（估計值）
4. ``GET /api/pop/growth``      每 15 分鐘採樣一次、估算出的每日新增列數
5. ``GET /api/schedules``       pg_cron 排程狀態
6. ``POST /api/jobs/probe_all_throughput`` / ``setup_schedules`` / ``truncate_cron_jobs``
   觸發對應的 ``pipeline.py`` 操作（背景執行，前端輪詢 ``GET /api/jobs/current`` 看進度）
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pg_tool import PostgreSQLTool
from pipeline import Pipeline

from . import dag_builder, endpoint_docs, job_runner, row_counts

SNAPSHOT_INTERVAL_SECONDS = 15 * 60
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="資料庫 DEMO", version="0.1.0")


@app.on_event("startup")
async def _on_startup() -> None:
    asyncio.create_task(_snapshot_loop())


async def _snapshot_loop() -> None:
    """Every SNAPSHOT_INTERVAL_SECONDS, record pop row counts and print a growth report."""
    while True:
        try:
            inserted = await asyncio.to_thread(row_counts.snapshot_once)
            growth = await asyncio.to_thread(row_counts.get_growth_estimates)
            print(f"[db_demo] snapshot recorded for {inserted} pop tables")
            for g in growth:
                if g["estimated_daily_growth"] is not None:
                    print(
                        f"[db_demo]   {g['table_name']}: ~{g['estimated_daily_growth']} rows/day "
                        f"(over {g['hours_observed']}h, {g['sample_count']} samples)"
                    )
        except Exception as e:  # noqa: BLE001 - background task, log only
            print(f"[db_demo] snapshot_loop failed: {e}")
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)


@app.get("/api/dag")
async def api_dag():
    pipeline = await asyncio.to_thread(Pipeline)
    graph = await asyncio.to_thread(dag_builder.build_dag, pipeline)
    counts = await asyncio.to_thread(row_counts.get_pop_row_counts, pipeline._db_tool)
    for node in graph["nodes"]:
        node["pop_row_count"] = counts.get(node["id"])
    return graph


@app.get("/api/view/{name}/columns")
async def api_view_columns(name: str):
    db = PostgreSQLTool()
    rows = await asyncio.to_thread(
        db.fetch_all,
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'poc' AND table_name = %s
        ORDER BY ordinal_position;
        """,
        (name,),
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"poc.{name} 沒有欄位資訊（view 可能尚未建立，請先執行 Pipeline().create_views()）",
        )
    endpoints = await asyncio.to_thread(endpoint_docs.get_endpoint_docs, name)
    return {
        "table": name,
        "columns": [{"name": r[0], "type": r[1]} for r in rows],
        "endpoints": endpoints,
    }


@app.get("/api/pop/row_counts")
async def api_pop_row_counts():
    counts = await asyncio.to_thread(row_counts.get_pop_row_counts)
    return {"row_counts": counts}


@app.get("/api/pop/growth")
async def api_pop_growth():
    growth = await asyncio.to_thread(row_counts.get_growth_estimates)
    return {"growth": growth}


@app.post("/api/pop/snapshot")
async def api_pop_snapshot_now():
    """Manual trigger for a snapshot (in addition to the 15-min background loop)."""
    inserted = await asyncio.to_thread(row_counts.snapshot_once)
    return {"inserted": inserted}


@app.get("/api/schedules")
async def api_schedules():
    db = PostgreSQLTool()
    rows = await asyncio.to_thread(
        db.fetch_all,
        """
        SELECT j.jobid, j.jobname, j.schedule, j.command, j.active,
               lr.status, lr.start_time, lr.end_time
        FROM cron.job j
        LEFT JOIN LATERAL (
            SELECT status, start_time, end_time
            FROM cron.job_run_details d
            WHERE d.jobid = j.jobid
            ORDER BY start_time DESC
            LIMIT 1
        ) lr ON TRUE
        ORDER BY j.jobname;
        """,
    )
    schedules = [
        {
            "jobid": r[0],
            "jobname": r[1],
            "schedule": r[2],
            "command": r[3],
            "active": r[4],
            "last_status": r[5],
            "last_start_time": r[6].isoformat() if r[6] else None,
            "last_end_time": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]
    return {"schedules": schedules}


def _serialize_job(job: Optional[dict]) -> Optional[dict]:
    if job is None:
        return None
    return {
        "id": job["id"],
        "name": job["name"],
        "status": job["status"],
        "error": job["error"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "logs": job["logs"][-500:],
    }


@app.get("/api/jobs/current")
async def api_jobs_current():
    return {"job": _serialize_job(job_runner.get_current_job())}


@app.post("/api/jobs/probe_all_throughput")
async def api_run_probe_all_throughput(restart: bool = Query(True)):
    def _task() -> None:
        Pipeline().probe_all_throughput(restart=restart)

    try:
        job = job_runner.start_job("probe_all_throughput", _task)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"job": _serialize_job(job)}


@app.post("/api/jobs/setup_schedules")
async def api_run_setup_schedules():
    def _task() -> None:
        Pipeline().setup_schedules()

    try:
        job = job_runner.start_job("setup_schedules", _task)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"job": _serialize_job(job)}


@app.post("/api/jobs/truncate_cron_jobs")
async def api_run_truncate_cron_jobs():
    def _task() -> None:
        Pipeline().truncate_cron_jobs()

    try:
        job = job_runner.start_job("truncate_cron_jobs", _task)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"job": _serialize_job(job)}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))
