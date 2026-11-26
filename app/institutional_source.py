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
  容器內 /tmp（重啟即消失）。只有上游**明確表示當日無資料**（TWSE stat「很抱歉，沒有符合條件的資料」、
  TPEx stat ok + 空表）才寫入空 dict；限流 / 封鎖 / 其他錯誤回應視為上游失敗，不寫 cache（I010）。

**擴充空間**：`_fetch_per_day_market` 為 (market, date) 粒度純函式，未來要做
逐月歷史補抓、DB 落地或批次跑滿全交易日，可直接複用；本次不做 DB 寫入。
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

from .sources import (
    SourceError,
    _classify_http_exc,
    _record_source_error,
    load_basic_table,
    record_bad_payload,
    tpex_payload_state,
    twse_payload_state,
)

logger = logging.getLogger("twstock_api.institutional")

CACHE_ROOT = Path("/tmp/institutional_cache")

# TWSE T86 (per-day 全市場) — 官方最早提供日期：民國 101/5/2 = 2012-05-02
INSTITUTIONAL_MIN_DATE = date(2012, 5, 2)

# 查詢日不是交易日（週末、國定假日、春節）時，往前找最近交易日的日曆天數上限。
# 全市場 payload 依日期磁碟快取、跨股共用，回溯的額外上游請求只在第一次發生。
MAX_LOOKBACK_DAYS = 10

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

async def _fetch_twse_t86_day(d: date) -> Optional[dict[str, dict]]:
    """回傳 stk_code -> row dict（該日全上市股票三大法人明細）。

    * 交易日 → payload（cache）
    * 非交易日（TWSE 明確回「沒有符合條件的資料」）→ `{}`（cache；今天除外，可能只是尚未收盤）
    * 上游失敗（HTTP 錯誤 / 封鎖頁 / 其他 stat）→ `None`（不 cache）
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
        return None

    state = twse_payload_state(data)
    if state == "error":
        record_bad_payload("TWSE", url, data)
        return None
    parsed = _parse_twse_t86_payload(data) if state == "ok" else {}
    if state == "ok" and data.get("data") and not parsed:
        # 上游有資料卻一列都解析不出來 = 版面變了；絕不能快取成「非交易日」（I010 真正主因）
        record_bad_payload("TWSE", url, {"stat": f"unparsed layout fields={data.get('fields')}"})
        return None
    if parsed or d < date.today():
        _cache_write("twse", d, parsed)
    return parsed


# T86 欄位版面依日期不同（2026-11-25 實測），一律以 `fields` 名稱對應欄位：
#   * 2012-05-02 起：12 欄，自營商未拆自行買賣／避險
#   * 2014-12 起：16 欄，自營商拆自行買賣／避險
#   * 2017-12-18 起：19 欄，外資拆成「外陸資（不含外資自營商）」與「外資自營商」
# 舊版只接受 19 欄，2017-12-18 前每個交易日都解析成空 dict 並被快取成「非交易日」（I010 主因）。
# 2017-12-18 前的「外資買賣超股數」含外資自營商，對應到 foreign_investors_net_buy_sell，
# foreign_dealers_net_buy_sell 為 None；12 欄版面沒有自行買賣／避險，對應欄位為 None。
_T86_FIELD_NAMES = {
    "foreign_investors_net_buy_sell": ("外陸資買賣超股數(不含外資自營商)", "外資買賣超股數"),
    "foreign_dealers_net_buy_sell": ("外資自營商買賣超股數",),
    "investment_trust_net_buy_sell": ("投信買賣超股數",),
    "dealers_net_buy_sell": ("自營商買賣超股數",),
    "dealers_proprietary_net_buy_sell": ("自營商買賣超股數(自行買賣)",),
    "dealers_hedge_net_buy_sell": ("自營商買賣超股數(避險)",),
    "total_institutional_net_buy_sell": ("三大法人買賣超股數",),
}
# 沒有 `fields` 時沿用 19 欄位置
_T86_DEFAULT_FIELDS = [
    "證券代號", "證券名稱", "", "", "外陸資買賣超股數(不含外資自營商)", "", "", "外資自營商買賣超股數",
    "", "", "投信買賣超股數", "自營商買賣超股數", "", "", "自營商買賣超股數(自行買賣)", "", "",
    "自營商買賣超股數(避險)", "三大法人買賣超股數",
]


def _parse_twse_t86_payload(data: dict) -> dict[str, dict]:
    """解析 TWSE T86 JSON payload，回 stk_code -> row（依 `fields` 名稱對應，支援 12／16／19 欄版面）。"""
    if not isinstance(data, dict) or (data.get("stat") or "").upper() != "OK":
        return {}
    fields = [str(f).strip() for f in (data.get("fields") or _T86_DEFAULT_FIELDS)]
    index = {name: i for i, name in enumerate(fields) if name}
    if "三大法人買賣超股數" not in index:
        return {}
    cols = {key: next((index[n] for n in names if n in index), None) for key, names in _T86_FIELD_NAMES.items()}
    out: dict[str, dict] = {}
    for row in data.get("data") or []:
        if not row or len(row) < len(fields):
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        out[code] = {"stock_name": str(row[1]).strip()}
        for key, i in cols.items():
            out[code][key] = _parse_int(row[i]) if i is not None else None
    return out


# =============================================================================
# TPEx dailyTrade — per-day 全市場 JSON
# =============================================================================

async def _fetch_tpex_daily_trade(d: date) -> Optional[dict[str, dict]]:
    """回傳 stk_code -> row dict（該日全上櫃股票三大法人明細）；語意同 `_fetch_twse_t86_day`。"""
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
        return None

    if tpex_payload_state(data, d) == "error":
        record_bad_payload("TPEx", url, data)
        return None
    parsed = _parse_tpex_daily_trade_payload(data)
    has_rows = any(isinstance(t, dict) and t.get("data") for t in data.get("tables") or [])
    if has_rows and not parsed:
        record_bad_payload("TPEx", url, {"stat": "unparsed dailyTrade layout"})
        return None
    if parsed or d < date.today():
        _cache_write("tpex", d, parsed)
    return parsed


def _parse_tpex_daily_trade_payload(data: dict) -> dict[str, dict]:
    """解析 TPEx dailyTrade JSON payload，回 stk_code -> row。

    資料表不一定在 tables[0]（舊日期 tables[0] 為 {}、資料在 tables[1]），取第一個有資料的表。
    兩種版面（2026-11-25 實測）：
      * 24 欄（近年）：[0]代號 [1]名稱 [2-4]外資及陸資(不含自營商) [5-7]外資自營商 [8-10]外資及陸資合計
        [11-13]投信 [14-16]自營商自行買賣 [17-19]自營商避險 [20-22]自營商合計 [23]三大法人合計
      * 16 欄（舊）：[0]代號 [1]名稱 [2-4]外資及陸資（含自營商） [5-7]投信 [8]自營淨買
        [9-11]自營商自行買賣 [12-14]自營商避險 [15]三大法人買賣超；沒有外資自營商（None）
    """
    if not isinstance(data, dict) or (data.get("stat") or "").lower() != "ok":
        return {}
    rows = next((t.get("data") for t in data.get("tables") or [] if isinstance(t, dict) and t.get("data")), [])
    out: dict[str, dict] = {}
    for row in rows:
        if not row:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        if len(row) >= 24:
            idx = {"foreign_investors_net_buy_sell": 4, "foreign_dealers_net_buy_sell": 7,
                   "investment_trust_net_buy_sell": 13, "dealers_net_buy_sell": 22,
                   "dealers_proprietary_net_buy_sell": 16, "dealers_hedge_net_buy_sell": 19,
                   "total_institutional_net_buy_sell": 23}
        elif len(row) >= 16:
            idx = {"foreign_investors_net_buy_sell": 4, "foreign_dealers_net_buy_sell": None,
                   "investment_trust_net_buy_sell": 7, "dealers_net_buy_sell": 8,
                   "dealers_proprietary_net_buy_sell": 11, "dealers_hedge_net_buy_sell": 14,
                   "total_institutional_net_buy_sell": 15}
        else:
            continue
        out[code] = {"stock_name": str(row[1]).strip()}
        for key, i in idx.items():
            out[code][key] = _parse_int(row[i]) if i is not None else None
    return out


# =============================================================================
# 對外主函式
# =============================================================================

async def _fetch_per_day_market(market: str, d: date) -> Optional[dict[str, dict]]:
    """依市場別呼叫對應 per-day 全市場 endpoint（`None` = 上游失敗）。未來歷史補抓可直接複用。"""
    if market == "上市":
        return await _fetch_twse_t86_day(d)
    if market == "上櫃":
        return await _fetch_tpex_daily_trade(d)
    return {}


async def _latest_trading_day(market: str, d: date) -> tuple[date, Optional[dict[str, dict]]]:
    """(交易日, 全市場 payload)：查詢日或之前最近、且在 MAX_LOOKBACK_DAYS 內有資料的交易日。

    * 回溯範圍內都沒有交易 → `(d, {})`
    * 途中上游失敗 → `(d, None)`：立即停止，不把失敗當成假日一路往前打
    """
    for back in range(MAX_LOOKBACK_DAYS + 1):
        day = d - timedelta(days=back)
        if day < INSTITUTIONAL_MIN_DATE:
            break
        day_map = await _fetch_per_day_market(market, day)
        if day_map is None:
            return d, None
        if day_map:
            return day, day_map
    return d, {}


async def get_institutional_net_buy_sell(stk_code: str, d: date) -> dict:
    """取得指定股票、指定交易日的三大法人買賣超明細。

    回傳統一 dict schema（見 module docstring）；欄位若上游未提供則為 None。

    * 找不到 stk_code（基本資料）→ `found=False`, `market=None`
    * 找到 stk_code，但回溯範圍內沒有交易日、或該股當日不在 payload
      → `found=True, market="上市"|"上櫃", row=None`
    * 上游失敗 → `found=True, row=None, reason="upstream_error"`（endpoint 回 502，不被 DB 快取）
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
    data_day, day_map = await _latest_trading_day(market, d)
    row = (day_map or {}).get(stk_code)

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
            "reason": "upstream_error" if day_map is None else None,
        }

    # stock_name 以基本資料 short_name 為主，退回 payload 上的名稱
    stock_name = (info.get("short_name") or "").strip() or row.get("stock_name")
    return {
        "found": True,
        "stk_code": stk_code,
        "market": market or None,
        "trade_date": d.isoformat(),
        "data_date": data_day.isoformat(),
        "row": {
            "trade_date": data_day.isoformat(),
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
