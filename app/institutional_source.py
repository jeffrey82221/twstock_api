"""三大法人買賣超日報資料源（TWSE T86 + TPEx dailyTrade 融合）。

提供 `get_institutional_net_buy_sell(stk_code, d)` — 依股票市場 (上市 / 上櫃) 呼叫
對應的官方 per-day 全市場 endpoint，從 payload filter 出該股：

  * 上市：`https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=json`
  * 上櫃：`https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?date=YYYY/MM/DD&type=Daily&sect=EW&response=json`

同一日多支股票查詢共用單一 payload（磁碟 cache 命中），適合當日 / 監控用途。

輸出統一 schema：
  {
    trade_date, stk_code, stock_name,
    foreign_investors_net_buy_sell,       # 外陸資（不含外資自營商）買賣超股數
    foreign_dealers_net_buy_sell,         # 外資自營商買賣超股數
    investment_trust_net_buy_sell,        # 投信買賣超股數
    dealers_net_buy_sell,                 # 自營商合計買賣超股數（自行買賣 + 避險）
    dealers_proprietary_net_buy_sell,     # 自營商自行買賣買賣超股數
    dealers_hedge_net_buy_sell,           # 自營商避險買賣超股數
    total_institutional_net_buy_sell,     # 三大法人買賣超股數合計（上游直接提供，非本 backend 自行加總）
  }

單位：股（TWSE / TPEx 上游本 endpoint 皆為股，無需 ×1000 轉換）。

**磁碟 cache**（per-day 全市場 payload）：
  /tmp/institutional_cache/{market}/{YYYYMMDD}.json.gz
  容器內 /tmp（重啟即消失）；非交易日 / 缺 payload 一樣寫入空 dict 避免重打。

**擴充空間**：`_fetch_per_day_market` 為 (market, date) 粒度純函式，未來要做
逐月歷史補抓、DB 落地或批次跑滿全交易日，可直接複用；本次不做 DB 寫入。
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from .sources import (
    SourceError,
    _classify_http_exc,
    _record_source_error,
    load_basic_table,
)

logger = logging.getLogger("twstock_api.institutional")

CACHE_ROOT = Path("/tmp/institutional_cache")

# TWSE T86 (per-day 全市場) — 官方最早提供日期：民國 101/5/2 = 2012-05-02
INSTITUTIONAL_MIN_DATE = date(2012, 5, 2)

TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_DAILY_TRADE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"


# =============================================================================
# 內部：磁碟 cache
# =============================================================================

def _cache_path(market: str, d: date) -> Path:
    return CACHE_ROOT / market / f"{d.strftime('%Y%m%d')}.json.gz"


def _cache_read(market: str, d: date) -> Optional[dict]:
    p = _cache_path(market, d)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("institutional cache read failed %s: %s (discarding)", p, e)
        try:
            p.unlink()
        except Exception:
            pass
        return None


def _cache_write(market: str, d: date, payload: dict) -> None:
    p = _cache_path(market, d)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp.replace(p)
    except Exception as e:
        logger.warning("institutional cache write failed %s: %s", p, e)


# =============================================================================
# 內部：欄位工具
# =============================================================================

def _parse_int(s: Any) -> Optional[int]:
    """把含千分位逗號 / 前導 + / 空白的字串轉 int；轉不出來回 None。"""
    if s is None:
        return None
    txt = str(s).strip().replace(",", "").lstrip("+")
    if txt in ("", "-", "--", "X", "x"):
        return None
    try:
        return int(float(txt))
    except Exception:
        return None


# =============================================================================
# TWSE T86 — per-day 全市場 JSON
# =============================================================================

async def _fetch_twse_t86_day(d: date) -> dict[str, dict]:
    """回傳 stk_code -> row dict（該日全上市股票三大法人明細）。
    cache miss 時打上游、寫磁碟 cache；非交易日回 {}（也 cache 空 dict）。
    """
    cached = _cache_read("twse", d)
    if cached is not None:
        return cached

    url = f"{TWSE_T86_URL}?date={d.strftime('%Y%m%d')}&selectType=ALL&response=json"
    data: Optional[dict] = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (twstock_api)"},
            ) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))
    if data is None:
        assert last_exc is not None
        status, msg, rl = _classify_http_exc(last_exc, url)
        _record_source_error(SourceError(
            source="TWSE", url=url, status_code=status, message=msg, is_rate_limited=rl,
        ))
        return {}

    parsed = _parse_twse_t86_payload(data)
    _cache_write("twse", d, parsed)
    return parsed


def _parse_twse_t86_payload(data: dict) -> dict[str, dict]:
    """解析 TWSE T86 JSON payload，回 stk_code -> row。

    T86 payload 19 欄位順序：
      [0]證券代號  [1]證券名稱
      [2-4]外陸資買/賣/買賣超股數(不含外資自營商)
      [5-7]外資自營商買/賣/買賣超股數
      [8-10]投信買/賣/買賣超股數
      [11]自營商買賣超股數（合計）
      [12-14]自營商自行買賣買/賣/買賣超股數
      [15-17]自營商避險買/賣/買賣超股數
      [18]三大法人買賣超股數
    """
    if not isinstance(data, dict) or (data.get("stat") or "").upper() != "OK":
        return {}
    rows = data.get("data") or []
    out: dict[str, dict] = {}
    for row in rows:
        if not row or len(row) < 19:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        name = str(row[1]).strip()
        out[code] = {
            "stock_name": name,
            "foreign_investors_net_buy_sell": _parse_int(row[4]),
            "foreign_dealers_net_buy_sell": _parse_int(row[7]),
            "investment_trust_net_buy_sell": _parse_int(row[10]),
            "dealers_net_buy_sell": _parse_int(row[11]),
            "dealers_proprietary_net_buy_sell": _parse_int(row[14]),
            "dealers_hedge_net_buy_sell": _parse_int(row[17]),
            "total_institutional_net_buy_sell": _parse_int(row[18]),
        }
    return out


# =============================================================================
# TPEx dailyTrade — per-day 全市場 JSON
# =============================================================================

async def _fetch_tpex_daily_trade(d: date) -> dict[str, dict]:
    """回傳 stk_code -> row dict（該日全上櫃股票三大法人明細）。"""
    cached = _cache_read("tpex", d)
    if cached is not None:
        return cached

    url = (
        f"{TPEX_DAILY_TRADE_URL}?date={d.strftime('%Y/%m/%d')}"
        f"&type=Daily&sect=EW&id=&response=json"
    )
    data: Optional[dict] = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (twstock_api)"},
            ) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))
    if data is None:
        assert last_exc is not None
        status, msg, rl = _classify_http_exc(last_exc, url)
        _record_source_error(SourceError(
            source="TPEx", url=url, status_code=status, message=msg, is_rate_limited=rl,
        ))
        return {}

    parsed = _parse_tpex_daily_trade_payload(data)
    _cache_write("tpex", d, parsed)
    return parsed


def _parse_tpex_daily_trade_payload(data: dict) -> dict[str, dict]:
    """解析 TPEx dailyTrade JSON payload，回 stk_code -> row。

    TPEx 24 欄位順序（依 政府開放資料平臺 上櫃股票三大法人買賣明細資訊定義）：
      [0]代號  [1]名稱
      [2-4]外資及陸資（不含自營商）買/賣/超
      [5-7]外資自營商買/賣/超
      [8-10]外資及陸資（合計）買/賣/超
      [11-13]投信買/賣/超
      [14-16]自營商自行買賣買/賣/超
      [17-19]自營商避險買/賣/超
      [20-22]自營商合計買/賣/超
      [23]三大法人買賣超股數合計
    """
    if not isinstance(data, dict) or (data.get("stat") or "").lower() != "ok":
        return {}
    tables = data.get("tables") or []
    if not tables:
        return {}
    rows = tables[0].get("data") or []
    out: dict[str, dict] = {}
    for row in rows:
        if not row or len(row) < 24:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        name = str(row[1]).strip()
        out[code] = {
            "stock_name": name,
            "foreign_investors_net_buy_sell": _parse_int(row[4]),
            "foreign_dealers_net_buy_sell": _parse_int(row[7]),
            "investment_trust_net_buy_sell": _parse_int(row[13]),
            "dealers_net_buy_sell": _parse_int(row[22]),
            "dealers_proprietary_net_buy_sell": _parse_int(row[16]),
            "dealers_hedge_net_buy_sell": _parse_int(row[19]),
            "total_institutional_net_buy_sell": _parse_int(row[23]),
        }
    return out


# =============================================================================
# 對外主函式
# =============================================================================

async def _fetch_per_day_market(market: str, d: date) -> dict[str, dict]:
    """依市場別呼叫對應 per-day 全市場 endpoint。未來歷史補抓可直接複用。"""
    if market == "上市":
        return await _fetch_twse_t86_day(d)
    if market == "上櫃":
        return await _fetch_tpex_daily_trade(d)
    return {}


async def get_institutional_net_buy_sell(stk_code: str, d: date) -> dict:
    """取得指定股票、指定交易日的三大法人買賣超明細。

    回傳統一 dict schema（見 module docstring）；欄位若上游未提供則為 None。

    * 找不到 stk_code（基本資料）→ `found=False`, `market=None`
    * 找到 stk_code 但當日全市場 payload 為空（非交易日 / 當日尚未收盤 / 上游失敗）
      → `found=True, market="上市"|"上櫃", row=None`
    """
    basic = await load_basic_table()
    info = basic.get(stk_code)
    if not info:
        return {
            "found": False,
            "stk_code": stk_code,
            "market": None,
            "trade_date": d.isoformat(),
            "row": None,
            "source": None,
        }

    market = info.get("market") or ""
    day_map = await _fetch_per_day_market(market, d)
    row = day_map.get(stk_code)

    if market == "上市":
        source = "TWSE T86 (per-day)"
    elif market == "上櫃":
        source = "TPEx dailyTrade (per-day)"
    else:
        source = None

    if not row:
        return {
            "found": True,
            "stk_code": stk_code,
            "market": market or None,
            "trade_date": d.isoformat(),
            "row": None,
            "source": source,
        }

    # stock_name 以基本資料 short_name 為主，退回 payload 上的名稱
    stock_name = (info.get("short_name") or "").strip() or row.get("stock_name")
    return {
        "found": True,
        "stk_code": stk_code,
        "market": market or None,
        "trade_date": d.isoformat(),
        "row": {
            "trade_date": d.isoformat(),
            "stk_code": stk_code,
            "stock_name": stock_name,
            "foreign_investors_net_buy_sell": row.get("foreign_investors_net_buy_sell"),
            "foreign_dealers_net_buy_sell": row.get("foreign_dealers_net_buy_sell"),
            "investment_trust_net_buy_sell": row.get("investment_trust_net_buy_sell"),
            "dealers_net_buy_sell": row.get("dealers_net_buy_sell"),
            "dealers_proprietary_net_buy_sell": row.get("dealers_proprietary_net_buy_sell"),
            "dealers_hedge_net_buy_sell": row.get("dealers_hedge_net_buy_sell"),
            "total_institutional_net_buy_sell": row.get("total_institutional_net_buy_sell"),
        },
        "source": source,
    }
