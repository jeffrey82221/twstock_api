"""FinMind TaiwanStockNews source adapter."""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from typing import Any

import httpx
from cachetools import TTLCache

API_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockNews"
MIN_DATE = date(2019, 1, 1)

_day_cache: TTLCache[tuple[str, date], list[dict[str, Any]]] = TTLCache(maxsize=4096, ttl=21600)
_cache_lock = asyncio.Lock()


async def _fetch_day(stock_id: str, day: date) -> list[dict[str, Any]]:
    cache_key = (stock_id, day)
    async with _cache_lock:
        cached = _day_cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {}
    token = os.getenv("FINMIND_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"dataset": DATASET, "data_id": stock_id, "start_date": day.isoformat()}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(API_URL, params=params, headers=headers)
        response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 200:
        raise RuntimeError(payload.get("msg") or "FinMind returned an error")
    rows = payload.get("data") or []
    async with _cache_lock:
        _day_cache[cache_key] = rows
    return rows


def _row_datetime(row: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))


async def get_news(stock_id: str, as_of: date) -> dict[str, Any]:
    normalized_id = stock_id.strip()
    if not normalized_id:
        raise ValueError("stock_id must not be empty")
    rows = await _fetch_day(normalized_id, as_of)
    if rows:
        return {
            "found": True,
            "stock_id": normalized_id,
            "as_of": as_of.isoformat(),
            "data_date": as_of.isoformat(),
            "items": sorted(rows, key=_row_datetime, reverse=True),
            "source": "FinMind TaiwanStockNews",
        }
    return {
        "found": False,
        "stock_id": normalized_id,
        "as_of": as_of.isoformat(),
        "data_date": None,
        "items": [],
        "source": "FinMind TaiwanStockNews",
    }