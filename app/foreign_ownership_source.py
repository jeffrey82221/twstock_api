"""TWSE MI_QFIIS 外資及陸資投資持股統計資料源。"""
from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from .sources import SourceError, _classify_http_exc, _record_source_error

MI_QFIIS_URL = "https://www.twse.com.tw/fund/MI_QFIIS"
MIN_DATE = date(2004, 2, 11)
REQUEST_DELAY_SECONDS = 3.0
CACHE_ROOT = Path("/tmp/foreign_ownership_cache")


def _parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(value: str) -> Optional[date]:
    try:
        year, month, day = value.strip().split("/")
        return date(int(year) + 1911, int(month), int(day))
    except (ValueError, TypeError):
        return None


def parse_mi_qfiis_csv(content: bytes, report_date: date) -> dict[str, dict]:
    """解析 TWSE Big5 CSV，回傳股票代號到標準化 row 的 mapping。"""
    text = content.decode("big5", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    header_index = next(
        (index for index, row in enumerate(rows) if row and row[0].strip() == "證券代號"),
        None,
    )
    if header_index is None:
        return {}

    result: dict[str, dict] = {}
    for row in rows[header_index + 1:]:
        if len(row) < 10:
            continue
        stock_id = row[0].strip().lstrip("=").strip('"')
        if not stock_id or not stock_id[0].isdigit():
            continue
        last_change = _parse_date(row[11]) if len(row) > 11 else None
        result[stock_id] = {
            "trade_date": report_date.isoformat(),
            "stock_id": stock_id,
            "stock_name": row[1].strip(),
            "issued_shares": _parse_number(row[3]),
            "foreign_investment_available_shares": _parse_number(row[4]),
            "foreign_investment_shares": _parse_number(row[5]),
            "foreign_investment_available_ratio_pct": _parse_number(row[6]),
            "foreign_investment_ratio_pct": _parse_number(row[7]),
            "foreign_investment_limit_ratio_pct": _parse_number(row[8]),
            "china_investment_limit_ratio_pct": _parse_number(row[9]),
            "change_reason": row[10].strip() if len(row) > 10 else None,
            "last_change_date": last_change.isoformat() if last_change else None,
        }
    return result


def _cache_path(report_date: date) -> Path:
    return CACHE_ROOT / f"{report_date:%Y%m%d}.json.gz"


def _cache_read(report_date: date) -> Optional[dict]:
    path = _cache_path(report_date)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        try:
            path.unlink()
        except OSError:
            pass
        return None


def _cache_write(report_date: date, payload: dict) -> None:
    path = _cache_path(report_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    temporary.replace(path)


async def _fetch_day(report_date: date) -> dict[str, dict]:
    cached = _cache_read(report_date)
    if cached is not None:
        return cached
    url = f"{MI_QFIIS_URL}?response=csv&date={report_date:%Y%m%d}&selectType=ALLBUT0999"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (twstock_api)"})
            response.raise_for_status()
        payload = parse_mi_qfiis_csv(response.content, report_date)
    except Exception as exc:
        status, message, rate_limited = _classify_http_exc(exc, url)
        _record_source_error(SourceError(
            source="TWSE", url=url, status_code=status, message=message, is_rate_limited=rate_limited,
        ))
        return {}
    _cache_write(report_date, payload)
    return payload


async def get_foreign_ownership(stock_id: str, as_of: date) -> Optional[dict]:
    """取得指定股票在 as_of 或之前最近可得交易日的外資持股資料。"""
    current = as_of
    previous_was_cache_miss = False
    while current >= MIN_DATE:
        was_cache_miss = _cache_read(current) is None
        if was_cache_miss and previous_was_cache_miss:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
        day_rows = await _fetch_day(current)
        previous_was_cache_miss = was_cache_miss
        row = day_rows.get(stock_id)
        if row is not None:
            return row
        current -= timedelta(days=1)
    return None