"""OHLCV 歷史行情資料源（TWSE + TPEx 融合）。

提供 `get_ohlcv(stk_code, from_date, to_date)` — 依 (股票, 日期範圍) 智能切換上游：

  * range_days ≤ 7 → 逐日呼叫 per-day 全市場 endpoint，從全市場 payload filter 出該股：
      - 上市：`https://www.twse.com.tw/exchangeReport/MI_INDEX?response=csv&date=YYYYMMDD&type=ALLBUT0999`（Big5 CSV）
      - 上櫃：`https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&id=&response=json&type=EW`
    優點：同一日多支股票查詢共用單一 payload；適合當日 / 本週監控。
  * range_days > 7  → 逐月呼叫 per-stock-per-month endpoint（單股單月整月日 K）：
      - 上市：`https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=XXXX`
      - 上櫃：`https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=XXXX&date=YYYY/MM/DD&id=&response=json`
    優點：一次回傳約 20 個交易日、成本 1/20；適合歷史回填。

輸出統一 schema（單位對齊到「股 / 元」）：
  {trade_date, open, high, low, close, volume, trade_value, transaction_count, change}

**磁碟 cache**（僅 per-day 全市場 payload）：
  /tmp/ohlcv_cache/{market}/{YYYYMMDD}.json.gz
  容器內 /tmp（重啟即消失）；per-stock 不 cache（combinatorial 過大且下游本身有 pg_ivm 物化）。
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import json
import logging
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, Optional

import httpx

from .sources import (
    SourceError,
    _classify_http_exc,
    _http_get,
    _record_source_error,
    load_basic_table,
)

logger = logging.getLogger("twstock_api.ohlcv")

CACHE_ROOT = Path("/tmp/ohlcv_cache")
SMART_SWITCH_DAY_THRESHOLD = 7  # ≤7 天走 per-day 全市場，>7 天走 per-stock-per-month
MAX_RANGE_DAYS = 366  # endpoint 對外契約：一次最多 366 天

# TWSE STOCK_DAY (per-stock-per-month) 官方 hard block：查詢日期小於此值 → server 回
#   {"stat": "查詢日期小於99年1月4日，請重新查詢!", "total": 0}
# MI_INDEX (per-day-market) 下限較寬（民國 93/2/11 = 2004-02-11），
# 因此上市個股任何 from < 此日期都必須強走 per_day_market 才能取得完整資料。
TWSE_STOCK_DAY_MIN_DATE = date(2010, 1, 4)


# =============================================================================
# 內部：磁碟 cache（per-day 全市場 payload）
# =============================================================================

def _cache_path(market: str, d: date) -> Path:
    return CACHE_ROOT / market / f"{d.strftime('%Y%m%d')}.json.gz"


def _cache_read(market: str, d: date) -> Optional[Any]:
    p = _cache_path(market, d)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # 損壞就丟掉重抓
        logger.warning("ohlcv cache read failed %s: %s (discarding)", p, e)
        try:
            p.unlink()
        except Exception:
            pass
        return None


def _cache_write(market: str, d: date, payload: Any) -> None:
    p = _cache_path(market, d)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子寫：先寫到 .tmp 再 rename
        tmp = p.with_suffix(p.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp.replace(p)
    except Exception as e:
        logger.warning("ohlcv cache write failed %s: %s", p, e)


# =============================================================================
# 內部：日期工具
# =============================================================================

def _month_starts(from_d: date, to_d: date) -> list[date]:
    """列出 [from_d, to_d] 涵蓋的所有月份的 1 號。"""
    out: list[date] = []
    y, m = from_d.year, from_d.month
    while (y, m) <= (to_d.year, to_d.month):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _iter_dates(from_d: date, to_d: date):
    d = from_d
    while d <= to_d:
        yield d
        d += timedelta(days=1)


def _parse_roc_slash(roc: str) -> Optional[date]:
    """民國年 'YYY/MM/DD' → date；失敗回 None。"""
    try:
        yyy, mm, dd = roc.strip().split("/")
        return date(int(yyy) + 1911, int(mm), int(dd))
    except Exception:
        return None


def _parse_num(s: Any) -> Optional[float]:
    """把含千分位逗號 / 前導 + / 空白的字串轉 float；轉不出來回 None。"""
    if s is None:
        return None
    txt = str(s).strip().replace(",", "").lstrip("+")
    if txt in ("", "-", "--", "X", "x"):
        return None
    try:
        return float(txt)
    except Exception:
        return None


# =============================================================================
# TWSE STOCK_DAY — per-stock-per-month
# =============================================================================

TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"


async def _fetch_twse_stock_day(stk_code: str, month_start: date) -> list[dict]:
    """回傳指定股票、指定月份的日 K 陣列（原始 payload rows，未 filter 日期範圍）。"""
    url = f"{TWSE_STOCK_DAY_URL}?response=json&date={month_start.strftime('%Y%m%d')}&stockNo={stk_code}"
    try:
        data = await _http_get(url, timeout=20.0, retries=2, source_name="TWSE")
    except Exception:
        return []
    if not isinstance(data, dict) or data.get("stat") != "OK":
        return []
    return _normalize_twse_stock_day_rows(data.get("data") or [], stk_code)


def _normalize_twse_stock_day_rows(rows: list[list], stk_code: str) -> list[dict]:
    """TWSE STOCK_DAY data 欄位順序：
       [0]日期(民國)  [1]成交股數  [2]成交金額  [3]開盤  [4]最高  [5]最低
       [6]收盤  [7]漲跌  [8]成交筆數  [9]註記
    """
    out: list[dict] = []
    for r in rows:
        if len(r) < 9:
            continue
        d = _parse_roc_slash(r[0])
        if not d:
            continue
        out.append({
            "trade_date": d.isoformat(),
            "stk_code": stk_code,
            "open": _parse_num(r[3]),
            "high": _parse_num(r[4]),
            "low": _parse_num(r[5]),
            "close": _parse_num(r[6]),
            "volume": _parse_num(r[1]),
            "trade_value": _parse_num(r[2]),
            "transaction_count": _parse_num(r[8]),
            "change": _parse_num(r[7]),
        })
    return out


# =============================================================================
# TWSE MI_INDEX — per-day 全市場 CSV（Big5, ms950）
# =============================================================================

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"


async def _fetch_twse_mi_index_day(d: date) -> dict[str, dict]:
    """回傳 stk_code -> row dict（該日全上市股票）。cache miss 時打上游、寫磁碟 cache。"""
    cached = _cache_read("twse", d)
    if cached is not None:
        return cached

    url = f"{TWSE_MI_INDEX_URL}?response=csv&date={d.strftime('%Y%m%d')}&type=ALLBUT0999"
    text: Optional[str] = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (twstock_api)"},
            ) as client:
                r = await client.get(url)
                r.raise_for_status()
                # TWSE 回 Big5（ms950）
                text = r.content.decode("ms950", errors="replace")
            break
        except Exception as e:
            last_exc = e
            if attempt < 2:
                await asyncio.sleep(0.6 * (attempt + 1))
    if text is None:
        assert last_exc is not None
        status, msg, rl = _classify_http_exc(last_exc, url)
        _record_source_error(SourceError(
            source="TWSE", url=url, status_code=status, message=msg, is_rate_limited=rl,
        ))
        return {}

    parsed = _parse_twse_mi_index_csv(text, d)
    # 非交易日或 payload 缺失 → parsed=={} 也 cache（避免每次重打），只是不會有內容
    _cache_write("twse", d, parsed)
    return parsed


def _parse_twse_mi_index_csv(text: str, d: date) -> dict[str, dict]:
    """從 MI_INDEX CSV 抽出「證券代號」個股區塊，回 stk_code -> row。

    CSV 是多區塊格式：找到 header 行含 `"證券代號","證券名稱",...` 之後開始收，
    每列以 `="XXXX"` 起頭（Excel 防前導 0 轉義），直到遇到非 `="` 起頭列或空白列。

    個股區塊欄位順序（16 欄）：
      [0]證券代號  [1]證券名稱  [2]成交股數  [3]成交筆數  [4]成交金額
      [5]開盤價  [6]最高價  [7]最低價  [8]收盤價  [9]漲跌(+/-)  [10]漲跌價差
      [11]最後揭示買價  [12]最後揭示買量  [13]最後揭示賣價  [14]最後揭示賣量  [15]本益比
    """
    out: dict[str, dict] = {}
    reader = csv.reader(StringIO(text))
    in_stock_block = False
    for row in reader:
        if not row:
            in_stock_block = False
            continue
        first = row[0].strip() if row[0] else ""
        if first == "證券代號" and len(row) >= 15:
            in_stock_block = True
            continue
        if not in_stock_block:
            continue
        # 有效個股列：first 形如 ="2330" 或直接 2330
        code = first.lstrip("=").strip('"').strip()
        if not code or not code[0].isdigit():
            in_stock_block = False
            continue
        if len(row) < 11:
            continue
        # 漲跌方向 (+/-) 與漲跌價差組合成 signed change
        sign = row[9].strip() if len(row) > 9 else ""
        diff_txt = row[10].strip() if len(row) > 10 else ""
        change: Optional[float] = _parse_num(diff_txt)
        if change is not None and sign == "-":
            change = -change
        out[code] = {
            "trade_date": d.isoformat(),
            "stk_code": code,
            "open": _parse_num(row[5]),
            "high": _parse_num(row[6]),
            "low": _parse_num(row[7]),
            "close": _parse_num(row[8]),
            "volume": _parse_num(row[2]),
            "trade_value": _parse_num(row[4]),
            "transaction_count": _parse_num(row[3]),
            "change": change,
        }
    return out


# =============================================================================
# TPEx tradingStock — per-stock-per-month JSON
# =============================================================================

TPEX_TRADING_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"


async def _fetch_tpex_trading_stock(stk_code: str, month_start: date) -> list[dict]:
    url = (
        f"{TPEX_TRADING_STOCK_URL}?code={stk_code}"
        f"&date={month_start.strftime('%Y/%m/%d')}&id=&response=json"
    )
    try:
        data = await _http_get(url, timeout=20.0, retries=2, source_name="TPEx")
    except Exception:
        return []
    if not isinstance(data, dict) or data.get("stat", "").lower() != "ok":
        return []
    tables = data.get("tables") or []
    rows = tables[0].get("data") if tables else []
    return _normalize_tpex_trading_stock_rows(rows or [], stk_code)


def _normalize_tpex_trading_stock_rows(rows: list[list], stk_code: str) -> list[dict]:
    """TPEx tradingStock data 欄位順序：
       [0]日期(民國)  [1]成交張數(*1000=股)  [2]成交仟元(*1000=元)
       [3]開盤  [4]最高  [5]最低  [6]收盤  [7]漲跌  [8]筆數
    """
    out: list[dict] = []
    for r in rows:
        if len(r) < 9:
            continue
        d = _parse_roc_slash(r[0])
        if not d:
            continue
        vol_shares = _parse_num(r[1])
        val_ktwd = _parse_num(r[2])
        out.append({
            "trade_date": d.isoformat(),
            "stk_code": stk_code,
            "open": _parse_num(r[3]),
            "high": _parse_num(r[4]),
            "low": _parse_num(r[5]),
            "close": _parse_num(r[6]),
            "volume": vol_shares * 1000 if vol_shares is not None else None,
            "trade_value": val_ktwd * 1000 if val_ktwd is not None else None,
            "transaction_count": _parse_num(r[8]),
            "change": _parse_num(r[7]),
        })
    return out


# =============================================================================
# TPEx dailyQuotes — per-day 全市場 JSON
# =============================================================================

TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


async def _fetch_tpex_daily_quotes(d: date) -> dict[str, dict]:
    """回傳 stk_code -> row dict（該日全上櫃股票）。"""
    cached = _cache_read("tpex", d)
    if cached is not None:
        return cached

    url = (
        f"{TPEX_DAILY_QUOTES_URL}?date={d.strftime('%Y/%m/%d')}"
        f"&id=&response=json&type=EW"
    )
    try:
        data = await _http_get(url, timeout=30.0, retries=2, source_name="TPEx")
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("stat", "").lower() != "ok":
        # 非交易日仍 cache 空 dict 避免重打
        _cache_write("tpex", d, {})
        return {}

    parsed = _parse_tpex_daily_quotes(data, d)
    _cache_write("tpex", d, parsed)
    return parsed


def _parse_tpex_daily_quotes(data: dict, d: date) -> dict[str, dict]:
    """TPEx dailyQuotes payload 結構：
       {stat: 'ok', date: 'YYYYMMDD', tables: [
           {title:'上櫃股票行情', fields:[...], data:[[...], ...]},
           {title:'管理股票', ...}, ...
       ]}
       取 tables[0]（上櫃股票行情主表）。欄位順序（實測）：
       [0]代號  [1]名稱  [2]收盤  [3]漲跌  [4]開盤  [5]最高  [6]最低
       [7]均價  [8]成交股數  [9]成交金額(元)  [10]成交筆數  [11..]買賣揭示 etc.
    """
    out: dict[str, dict] = {}
    tables = data.get("tables") or []
    if not tables:
        return out
    rows = tables[0].get("data") or []
    for r in rows:
        if len(r) < 11:
            continue
        code = str(r[0]).strip()
        if not code:
            continue
        out[code] = {
            "trade_date": d.isoformat(),
            "stk_code": code,
            "open": _parse_num(r[4]),
            "high": _parse_num(r[5]),
            "low": _parse_num(r[6]),
            "close": _parse_num(r[2]),
            "volume": _parse_num(r[8]),       # 已是「股」
            "trade_value": _parse_num(r[9]),  # 已是「元」
            "transaction_count": _parse_num(r[10]),
            "change": _parse_num(r[3]),
        }
    return out


# =============================================================================
# 對外主函式
# =============================================================================

async def _resolve_market(stk_code: str) -> Optional[str]:
    """回傳 '上市' / '上櫃' / None（查無）。"""
    basic = await load_basic_table()
    row = basic.get(stk_code)
    if not row:
        return None
    return row.get("market")


async def get_ohlcv(
    stk_code: str,
    from_date: date,
    to_date: date,
) -> dict:
    """依 (股票, 日期範圍) 智能切換上游取得日 OHLCV。

    回傳：
      {
        "found": bool,           # 是否有查到 market；range 內無交易日 rows=[] 但 found=True
        "stk_code": str,
        "market": "上市" | "上櫃" | None,
        "from_date": "YYYY-MM-DD",
        "to_date": "YYYY-MM-DD",
        "strategy": "per_day_market" | "per_stock_month",
        "rows": [ {trade_date, stk_code, open, high, low, close,
                   volume, trade_value, transaction_count, change}, ... ],
        "source": str,
      }

    交易日排序遞增。單位統一：volume=股、trade_value=元、金額欄位 float。
    """
    market = await _resolve_market(stk_code)
    if not market:
        return {
            "found": False,
            "stk_code": stk_code,
            "market": None,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "strategy": "",
            "rows": [],
            "source": "basic_table_lookup",
        }

    range_days = (to_date - from_date).days + 1
    strategy = "per_day_market" if range_days <= SMART_SWITCH_DAY_THRESHOLD else "per_stock_month"

    # Override：TWSE 個股在 STOCK_DAY hard-block 下限（2010-01-04）以前 →
    # 強制走 per_day_market（MI_INDEX），下限延伸到 2004-02-11。
    # TPEx endpoint 無官方下限，不需 override。
    if market == "上市" and from_date < TWSE_STOCK_DAY_MIN_DATE:
        strategy = "per_day_market"

    if strategy == "per_day_market":
        rows = await _collect_per_day_market(stk_code, market, from_date, to_date)
    else:
        rows = await _collect_per_stock_month(stk_code, market, from_date, to_date)

    rows.sort(key=lambda x: x["trade_date"])
    return {
        "found": True,
        "stk_code": stk_code,
        "market": market,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "strategy": strategy,
        "rows": rows,
        "source": (
            "TWSE MI_INDEX (per-day)" if strategy == "per_day_market" and market == "上市" else
            "TPEx dailyQuotes (per-day)" if strategy == "per_day_market" and market == "上櫃" else
            "TWSE STOCK_DAY (per-stock-per-month)" if market == "上市" else
            "TPEx tradingStock (per-stock-per-month)"
        ),
    }


async def _collect_per_day_market(
    stk_code: str, market: str, from_d: date, to_d: date,
) -> list[dict]:
    """逐日呼叫全市場 endpoint，filter 出指定 stk_code。同 batch 內並發拉最多 5 個日期。"""
    fetch = _fetch_twse_mi_index_day if market == "上市" else _fetch_tpex_daily_quotes
    days = list(_iter_dates(from_d, to_d))
    sem = asyncio.Semaphore(5)

    async def _one(d: date) -> Optional[dict]:
        async with sem:
            day_map = await fetch(d)
        return day_map.get(stk_code) if day_map else None

    results = await asyncio.gather(*(_one(d) for d in days))
    return [r for r in results if r is not None]


async def _collect_per_stock_month(
    stk_code: str, market: str, from_d: date, to_d: date,
) -> list[dict]:
    """逐月呼叫 per-stock-per-month endpoint，並 filter 到 [from_d, to_d]。並發最多 3 個月。"""
    fetch = _fetch_twse_stock_day if market == "上市" else _fetch_tpex_trading_stock
    months = _month_starts(from_d, to_d)
    sem = asyncio.Semaphore(3)

    async def _one(m: date) -> list[dict]:
        async with sem:
            return await fetch(stk_code, m)

    per_month = await asyncio.gather(*(_one(m) for m in months))
    from_iso, to_iso = from_d.isoformat(), to_d.isoformat()
    out: list[dict] = []
    for month_rows in per_month:
        for r in month_rows:
            if from_iso <= r["trade_date"] <= to_iso:
                out.append(r)
    return out
