"""TWSE securities borrowing and lending completion history (SBL t13sa870)."""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from .sources import SourceError, _classify_http_exc, _record_source_error, load_basic_table

SBL_URL = "https://www.twse.com.tw/SBL/t13sa870"
# Verified from 2330's full-range response: this is the earliest completion
# date, not the earliest lending transaction date.
SBL_MIN_DATE = date(2005, 1, 28)
WINDOW_DAYS = 31
CACHE_ROOT = Path("/tmp/twse_sbl_cache")


def _parse_roc_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    text = str(value).strip().replace("年", "/").replace("月", "/").replace("日", "")
    try:
        year, month, day = (int(part) for part in text.split("/")[:3])
        return date(year + 1911, month, day)
    except (TypeError, ValueError):
        return None


def _parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_sbl_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    positions = {field: index for index, field in enumerate(fields)}
    required = ("借券成交日期", "證券代號", "證券名稱", "交易方式", "成交數量(交易單位)", "成交費率(%)", "完成還券日收盤價", "完成還券日期", "借券天數")
    if any(field not in positions for field in required):
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        completion_date = _parse_roc_date(row[positions["完成還券日期"]])
        transaction_date = _parse_roc_date(row[positions["借券成交日期"]])
        if completion_date is None:
            continue
        result.append({
            "transaction_date": transaction_date.isoformat() if transaction_date else None,
            "stock_id": str(row[positions["證券代號"]]).strip(),
            "stock_name": str(row[positions["證券名稱"]]).strip(),
            "transaction_type": str(row[positions["交易方式"]]).strip(),
            "quantity_lots": _parse_number(row[positions["成交數量(交易單位)"]]),
            "fee_rate_pct": _parse_number(row[positions["成交費率(%)"]]),
            "completion_close_price": _parse_number(row[positions["完成還券日收盤價"]]),
            "completion_date": completion_date.isoformat(),
            "lending_days": int(row[positions["借券天數"]]) if str(row[positions["借券天數"]]).strip().isdigit() else None,
        })
    return result


def _cache_path(stock_id: str, start: date, end: date) -> Path:
    return CACHE_ROOT / f"{stock_id}_{start:%Y%m%d}_{end:%Y%m%d}.json.gz"


def _cache_read(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    temporary.replace(path)


async def _fetch_window(stock_id: str, start: date, end: date) -> list[dict[str, Any]]:
    path = _cache_path(stock_id, start, end)
    cached = _cache_read(path)
    if cached is not None:
        return cached.get("rows", [])
    params = {
        "response": "json", "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"), "stockNo": stock_id, "dateType": "B",
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (twstock_api)"}) as client:
            response = await client.get(SBL_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        rows = parse_sbl_payload(payload) if payload.get("stat") == "OK" else []
    except Exception as exc:
        url = str(response.url) if "response" in locals() else SBL_URL
        status, message, rate_limited = _classify_http_exc(exc, url)
        _record_source_error(SourceError(source="TWSE SBL", url=url, status_code=status, message=message, is_rate_limited=rate_limited))
        return []
    _cache_write(path, {"rows": rows})
    return rows


async def get_sbl_history(stock_id: str, as_of: date) -> dict[str, Any]:
    stock = str(stock_id).strip()
    basic = await load_basic_table()
    if stock not in basic:
        return {"found": False, "stock_id": stock, "as_of": as_of.isoformat(), "data_date": None, "records": [], "source": "TWSE SBL t13sa870"}

    end = as_of
    while end >= SBL_MIN_DATE:
        start = max(SBL_MIN_DATE, end - timedelta(days=WINDOW_DAYS - 1))
        rows = await _fetch_window(stock, start, end)
        rows = [row for row in rows if row["completion_date"] <= as_of.isoformat()]
        if rows:
            data_date = max(row["completion_date"] for row in rows)
            return {"found": True, "stock_id": stock, "as_of": as_of.isoformat(), "data_date": data_date, "records": rows, "source": "TWSE SBL t13sa870"}
        end = start - timedelta(days=1)
        await asyncio.sleep(0)
    return {"found": False, "stock_id": stock, "as_of": as_of.isoformat(), "data_date": None, "records": [], "source": "TWSE SBL t13sa870"}