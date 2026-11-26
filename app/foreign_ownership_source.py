"""外資及陸資投資持股統計資料源：上市 TWSE MI_QFIIS、上櫃 TPEx qfii（I006）。"""
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

from .sources import (
    SourceError,
    UpstreamFetchError,
    _classify_http_exc,
    _http_get,
    _record_source_error,
    load_basic_table,
    record_bad_payload,
    tpex_payload_state,
)

MI_QFIIS_URL = "https://www.twse.com.tw/fund/MI_QFIIS"
MIN_DATE = date(2004, 2, 11)
REQUEST_DELAY_SECONDS = 3.0
# as_of 當日無資料時最多往前回溯幾天。31 天對齊 PoC seed「每月 5 日取樣一次」的間隔；
# 原本無上限（一路回溯到 MIN_DATE），seed 第一列落在掛牌前時一列可打上千次 MI_QFIIS。
MAX_LOOKBACK_DAYS = 31
CACHE_ROOT = Path("/tmp/foreign_ownership_cache")

# 上櫃（I006）：TPEx 櫃檯買賣股票僑外資及陸資持股比例（每日全上櫃 JSON），實測最早有資料日 2007-05-02。
TPEX_QFII_URL = "https://www.tpex.org.tw/www/zh-tw/insti/qfii"
TPEX_MIN_DATE = date(2007, 5, 2)
SOURCE_TWSE = "TWSE MI_QFIIS"
SOURCE_TPEX = "TPEx qfii"


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

    header = [h.strip() for h in rows[header_index]]
    col = _qfiis_columns(header)
    result: dict[str, dict] = {}
    for row in rows[header_index + 1:]:
        if len(row) < 9:
            continue
        stock_id = row[0].strip().lstrip("=").strip('"')
        if not stock_id or not stock_id[0].isdigit():
            continue

        def cell(key: str) -> Optional[str]:
            i = col.get(key)
            return row[i] if i is not None and i < len(row) else None

        last_change = _parse_date(cell("last_change_date") or "")
        reason = cell("change_reason")
        result[stock_id] = {
            "trade_date": report_date.isoformat(),
            "stock_id": stock_id,
            "stock_name": row[1].strip(),
            "issued_shares": _parse_number(cell("issued_shares")),
            "foreign_investment_available_shares": _parse_number(cell("foreign_investment_available_shares")),
            "foreign_investment_shares": _parse_number(cell("foreign_investment_shares")),
            "foreign_investment_available_ratio_pct": _parse_number(cell("foreign_investment_available_ratio_pct")),
            "foreign_investment_ratio_pct": _parse_number(cell("foreign_investment_ratio_pct")),
            "foreign_investment_limit_ratio_pct": _parse_number(cell("foreign_investment_limit_ratio_pct")),
            "china_investment_limit_ratio_pct": _parse_number(cell("china_investment_limit_ratio_pct")),
            "change_reason": reason.strip() if reason is not None else None,
            "last_change_date": last_change.isoformat() if last_change else None,
        }
    return result


def _qfiis_columns(header: list[str]) -> dict[str, int]:
    """依表頭名稱找欄位（2026-11-25 實測兩種版面）：

    * 2009 年以前：11 欄「外資…」，沒有陸資上限欄
    * 之後：12 欄「外資及陸資…」，多「陸資法令投資上限比率」
    舊版用固定位置，舊版面的「與前日異動原因」被讀成陸資上限、「異動日期」被讀成異動原因。
    """
    col: dict[str, int] = {}
    for i, name in enumerate(header):
        if name == "發行股數":
            col["issued_shares"] = i
        elif name.endswith("尚可投資股數"):
            col["foreign_investment_available_shares"] = i
        elif name.endswith("持有股數"):
            col["foreign_investment_shares"] = i
        elif name.endswith("尚可投資比率"):
            col["foreign_investment_available_ratio_pct"] = i
        elif name.endswith("持股比率"):
            col["foreign_investment_ratio_pct"] = i
        elif name.startswith("陸資法令投資上限比率"):
            col["china_investment_limit_ratio_pct"] = i
        elif name.endswith("法令投資上限比率"):
            col["foreign_investment_limit_ratio_pct"] = i
        elif name.startswith("與前日異動原因"):
            col["change_reason"] = i
        elif name.startswith("最近一次"):
            col["last_change_date"] = i
    return col


def _cache_path(report_date: date, market: str = "twse") -> Path:
    # 上市沿用既有路徑（CACHE_ROOT 根目錄），上櫃放 tpex/ 子目錄
    root = CACHE_ROOT if market == "twse" else CACHE_ROOT / market
    return root / f"{report_date:%Y%m%d}.json.gz"


def _cache_read(report_date: date, market: str = "twse") -> Optional[dict]:
    path = _cache_path(report_date, market)
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


def _cache_write(report_date: date, payload: dict, market: str = "twse") -> None:
    path = _cache_path(report_date, market)
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
        if not _has_qfiis_header(response.content):
            # 非交易日的 CSV 仍有「證券代號」表頭（0 列資料）；沒有表頭 = 封鎖頁 / 錯誤頁（I010）
            raise UpstreamFetchError(f"MI_QFIIS response has no header row: {response.content[:80]!r}")
        payload = parse_mi_qfiis_csv(response.content, report_date)
    except Exception as exc:
        status, message, rate_limited = _classify_http_exc(exc, url)
        _record_source_error(SourceError(
            source="TWSE", url=url, status_code=status, message=message, is_rate_limited=rate_limited,
        ))
        # 不回 {}：空 payload 代表「非交易日」，會讓 get_foreign_ownership 繼續往前回溯，
        # 在 TWSE 封鎖（307 封鎖頁）時一路猛打上游、延長封鎖。
        raise UpstreamFetchError(message) from exc
    _cache_write(report_date, payload)
    return payload


def _has_qfiis_header(content: bytes) -> bool:
    return "證券代號" in content.decode("big5", errors="replace")


def _parse_pct(value: Any) -> Optional[float]:
    return _parse_number(str(value).replace("%", "")) if value is not None else None


def parse_tpex_qfii_payload(data: dict, report_date: date) -> dict[str, dict]:
    """TPEx qfii tables[0] 欄位：[排行, 代號, 名稱, 發行股數, 尚可投資股數, 持有股數,
    尚可投資比率, 持股比率, 法令投資上限比率, 備註]；輸出與 MI_QFIIS row 相同欄位。"""
    tables = data.get("tables") or []
    rows = (tables[0].get("data") or []) if tables else []
    result: dict[str, dict] = {}
    for row in rows:
        if len(row) < 9:
            continue
        stock_id = str(row[1]).strip()
        if not stock_id or not stock_id[0].isdigit():
            continue
        note = str(row[9]).strip() if len(row) > 9 else ""
        result[stock_id] = {
            "trade_date": report_date.isoformat(),
            "stock_id": stock_id,
            "stock_name": str(row[2]).strip(),
            "issued_shares": _parse_number(row[3]),
            "foreign_investment_available_shares": _parse_number(row[4]),
            "foreign_investment_shares": _parse_number(row[5]),
            "foreign_investment_available_ratio_pct": _parse_pct(row[6]),
            "foreign_investment_ratio_pct": _parse_pct(row[7]),
            "foreign_investment_limit_ratio_pct": _parse_pct(row[8]),
            "china_investment_limit_ratio_pct": None,
            "change_reason": note or None,
            "last_change_date": None,
        }
    return result


async def _fetch_tpex_day(report_date: date) -> dict[str, dict]:
    """上櫃版 `_fetch_day`：非交易日（stat ok + 空表）回 {} 並 cache；上游失敗拋 UpstreamFetchError。"""
    cached = _cache_read(report_date, "tpex")
    if cached is not None:
        return cached
    url = f"{TPEX_QFII_URL}?date={report_date:%Y/%m/%d}&response=json"
    try:
        data = await _http_get(url, timeout=30.0, retries=2, source_name="TPEx")
    except Exception as exc:
        raise UpstreamFetchError(f"TPEx qfii request failed: {exc}") from exc
    if tpex_payload_state(data, report_date) == "error":
        record_bad_payload("TPEx", url, data)
        raise UpstreamFetchError(f"TPEx qfii unexpected payload for {report_date.isoformat()}")
    payload = parse_tpex_qfii_payload(data, report_date)
    if payload or report_date < date.today():
        _cache_write(report_date, payload, "tpex")
    return payload


async def market_of(stock_id: str) -> Optional[str]:
    """`上市` / `上櫃` / None（基本資料查無）。"""
    return ((await load_basic_table()).get(stock_id) or {}).get("market")


def source_name(market: Optional[str]) -> str:
    return SOURCE_TPEX if market == "上櫃" else SOURCE_TWSE


async def get_foreign_ownership(
    stock_id: str, as_of: date, max_lookback_days: int = MAX_LOOKBACK_DAYS,
    market: Optional[str] = None,
) -> Optional[dict]:
    """取得指定股票在 as_of 或之前最近可得交易日的外資持股資料。

    回溯規則：
      * 最多往前 `max_lookback_days` 天（且不早於 MIN_DATE）。
      * 遇到「有資料的交易日（payload 非空）但沒有該股」即停止並回 None：MI_QFIIS 每個交易日
        都列出全部上市（TPEx 為全部上櫃）股，當天沒有就代表該股不在涵蓋範圍（尚未掛牌或已下市），再往前
        找只會白打上游。非交易日（payload 為空）才繼續往前。
      * 上游失敗（HTTP 錯誤 / 封鎖頁 / timeout）立即拋出 `UpstreamFetchError`，不繼續回溯。
      * `market="上櫃"` 走 TPEx qfii（2007-05-02 起）；其餘走 TWSE MI_QFIIS。
    """
    otc = market == "上櫃"
    fetch = _fetch_tpex_day if otc else _fetch_day
    cache_market = "tpex" if otc else "twse"
    floor = max(TPEX_MIN_DATE if otc else MIN_DATE, as_of - timedelta(days=max(max_lookback_days, 0)))
    current = as_of
    previous_was_cache_miss = False
    while current >= floor:
        was_cache_miss = _cache_read(current, cache_market) is None
        if was_cache_miss and previous_was_cache_miss:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
        day_rows = await fetch(current)
        previous_was_cache_miss = was_cache_miss
        row = day_rows.get(stock_id)
        if row is not None:
            return row
        if day_rows:  # 交易日但沒有該股 → 不在 MI_QFIIS 涵蓋範圍
            return None
        current -= timedelta(days=1)
    return None