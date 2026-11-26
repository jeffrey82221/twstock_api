"""全市場本益比 / 殖利率 / 股價淨值比彙總資料源（TWSE BWIBBU_d）。

提供 `get_market_valuation_summary(d)` — 呼叫 TWSE per-day 全市場個股本益比 payload，
依 `company_basic_info.paid_in_capital` 估流通股數，逐股反推每股淨值 / 每股純益 /
每股股利，最後在全市場層級加總計算三大指標：

    market_per   = Σ市值 / Σ純益          （市值加權；只納入 PER 有效個股）
    market_pbr   = Σ市值 / Σ淨值          （市值加權；只納入 PBR 有效個股）
    market_yield = Σ現金股利 / Σ市值      （市值加權；yield=0 也計入分母）

上游 endpoint（單日全上市；下市 / 特別股 / 已下櫃不含）：
    https://www.twse.com.tw/exchangeReport/BWIBBU_d?response=json&date=YYYYMMDD&selectType=ALL
    對應頁面：https://www.twse.com.tw/zh/trading/historical/bwibbu-day.html
    欄位：[代號, 名稱, 殖利率(%), 股利年度, 本益比, 股價淨值比, 財報年/季, 收盤價]

**股數推導**：台股面額慣例 10 元/股，`estimated_shares = paid_in_capital / 10`。
非嚴格流通股本（未扣庫藏股、面額非 10 元的股票會偏差），故本 endpoint 標示為
`estimated_market_cap_weighted`，PR 說明列出誤差來源。

**反推公式**（TWSE payload 只提供比率 + 收盤價）：
    每股淨值   BVPS = close / PBR              （PBR > 0 才成立）
    每股純益   EPS  = close / PER              （PER > 0 才成立）
    每股股利   DPS  = close * yield% / 100     （yield 可為 0；上游 yield 已為近四季）

**排除規則**（constituents 中 `excluded_reason` 註記）：
    * 收盤價缺 / 非數字 → 排除（無法算任何指標）
    * PBR 缺 / <= 0     → PBR 不加總；PER / yield 仍可能保留
    * PER 缺 / <= 0     → PER 不加總（EPS<=0 個股不計入分母 / 分子）
    * paid_in_capital 缺 → 完全排除（無法算市值權重）

排除細節：
    * per_included / pbr_included / yield_included 分別統計三個指標實際加總的股票數
    * `excluded_count` = 至少被一個指標排除的股票數（保留 payload 該股不進 sample）
    * `total_rows` = 上游 payload 原始筆數

磁碟 cache：`/tmp/valuation_cache/twse/{YYYYMMDD}.json.gz`（gzip JSON, atomic 寫 tmp then rename）
非交易日 / 上游空 payload 也 cache 空 dict，避免重打；但「今天」的空 payload 不 cache
（可能只是尚未收盤，收盤後要能重抓）。

**回溯最近交易日**：查詢日為非交易日（週末 / 國定假日）或當日尚未收盤時，往前逐日找
最近一個有資料的交易日（最多 `MAX_LOOKBACK_DAYS` 天，且不早於 `VALUATION_MIN_DATE`），
回應以 `data_date` 標示實際交易日，`date` 仍為查詢日。上游失敗時立即停止回溯
（`reason="upstream_error"`），避免在上游限流時連續多打。

**擴充空間**：`_fetch_twse_bwibbu_day(d)` 為 (date) 粒度純函式；
`iter_month_dates(year, month)` 提供未來逐月歷史補抓的日期序列 helper（本次不做 DB 寫入）。
"""
from __future__ import annotations

import asyncio
import calendar
import gzip
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

import httpx

from .sources import (
    SourceError,
    _classify_http_exc,
    _http_get,
    _record_source_error,
    load_basic_table,
    record_bad_payload,
    tpex_payload_state,
    twse_payload_state,
)

logger = logging.getLogger("twstock_api.valuation")

CACHE_ROOT = Path("/tmp/valuation_cache")

# BWIBBU_d 官方最早提供日：民國 94/9/2 = 2005-09-02（實測 2005 早期部分日期無資料，
# 但 2012 起穩定；保守取 2005-09-02 為下限）
VALUATION_MIN_DATE = date(2005, 9, 2)

TWSE_BWIBBU_URL = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"

# 上櫃（I005）：TPEx 上櫃股票個股本益比、殖利率及股價淨值比（每日全上櫃 payload）。
# 欄位：[代號, 名稱, 本益比, 每股股利, 股利年度, 殖利率(%), 股價淨值比]，沒有收盤價；
# 收盤價取同日 TPEx dailyQuotes（與 /api/ohlcv 共用磁碟 cache）。
# 實測最早有資料日 2007-01-02（更早的日期回 stat ok + 空表）。
TPEX_PE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
TPEX_VALUATION_MIN_DATE = date(2007, 1, 2)

# 回溯上限：台股最長連續休市為春節（約 9 天含週末），14 天留足餘裕
MAX_LOOKBACK_DAYS = 14

_CALCULATION_METHOD = (
    "estimated_market_cap_weighted "
    "(shares = paid_in_capital / 10; par-value convention)"
)
_SOURCE = "TWSE BWIBBU_d (per-day)"
_SOURCE_TPEX = "TPEx peQryDate + dailyQuotes (per-day)"

# 台股面額慣例：10 元 / 股（絕大多數上市普通股）
PAR_VALUE = 10.0


# =============================================================================
# 磁碟 cache
# =============================================================================

def _cache_path(d: date, market: str = "twse") -> Path:
    return CACHE_ROOT / market / f"{d.strftime('%Y%m%d')}.json.gz"


def _cache_read(d: date, market: str = "twse") -> Optional[dict]:
    p = _cache_path(d, market)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("valuation cache read failed %s: %s (discarding)", p, e)
        try:
            p.unlink()
        except Exception:
            pass
        return None


def _cache_write(d: date, payload: dict, market: str = "twse") -> None:
    p = _cache_path(d, market)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp.replace(p)
    except Exception as e:
        logger.warning("valuation cache write failed %s: %s", p, e)


# =============================================================================
# 欄位工具
# =============================================================================

def _parse_float(s: Any) -> Optional[float]:
    """含千分位逗號 / 空白 / `-` / `--` 的字串轉 float；轉不出來回 None。"""
    if s is None:
        return None
    txt = str(s).strip().replace(",", "")
    if txt in ("", "-", "--", "N/A"):
        return None
    try:
        return float(txt)
    except Exception:
        return None


# =============================================================================
# TWSE BWIBBU_d — per-day 全上市 payload
# =============================================================================

async def _fetch_twse_bwibbu_day(d: date) -> Optional[dict[str, dict]]:
    """回傳 stk_code -> row dict（該日全上市股票 PER / PBR / 殖利率 / 收盤價）。

    * 非交易日（TWSE 明確回「沒有符合條件的資料」）→ `{}`（cache；今天除外，可能只是尚未收盤）
    * 上游失敗（重試 3 次仍失敗、或限流 / 封鎖等其他 stat）→ `None`（不 cache），讓呼叫端與「沒資料」區分（I010）
    """
    cached = _cache_read(d)
    if cached is not None:
        return cached

    url = (
        f"{TWSE_BWIBBU_URL}?response=json"
        f"&date={d.strftime('%Y%m%d')}&selectType=ALL"
    )
    data: Optional[dict] = None
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True,
                verify=False,
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
    parsed = _parse_bwibbu_payload(data) if state == "ok" else {}
    if state == "ok" and data.get("data") and not parsed:
        record_bad_payload("TWSE", url, {"stat": f"unparsed layout fields={data.get('fields')}"})
        return None   # 有資料卻解析不出來 = 版面變了，不可快取成非交易日（I010）
    if parsed or d < date.today():
        _cache_write(d, parsed)
    return parsed


async def _fetch_tpex_pe_day(d: date) -> Optional[dict[str, dict]]:
    """上櫃版 `_fetch_twse_bwibbu_day`：回傳與 `_parse_bwibbu_payload` 相同 row 形狀。

    PE payload 與同日 dailyQuotes（收盤價）合併後才寫 cache；任一上游失敗回 `None`（不 cache）。
    """
    if d < TPEX_VALUATION_MIN_DATE:
        return {}
    cached = _cache_read(d, "tpex")
    if cached is not None:
        return cached

    url = f"{TPEX_PE_URL}?date={d.strftime('%Y/%m/%d')}&response=json"
    try:
        data = await _http_get(url, timeout=30.0, retries=2, source_name="TPEx")
    except Exception:
        return None
    if tpex_payload_state(data, d) == "error":
        record_bad_payload("TPEx", url, data)
        return None
    pe_rows = _parse_tpex_pe_payload(data)
    if pe_rows:
        from .ohlcv_source import fetch_tpex_daily_quotes_checked
        quotes = await fetch_tpex_daily_quotes_checked(d)
        if not quotes:          # None = 失敗；{} = PE 有資料但行情沒有 → 也視為失敗，不凍結
            return None
        for code, row in pe_rows.items():
            row["close_price"] = (quotes.get(code) or {}).get("close")
    if pe_rows or d < date.today():
        _cache_write(d, pe_rows, "tpex")
    return pe_rows


def _parse_tpex_pe_payload(data: dict) -> dict[str, dict]:
    """TPEx peQryDate tables[0] 欄位：[代號, 名稱, 本益比, 每股股利, 股利年度, 殖利率(%), 股價淨值比]。"""
    tables = data.get("tables") or []
    rows = (tables[0].get("data") or []) if tables else []
    out: dict[str, dict] = {}
    for row in rows:
        if not row or len(row) < 7:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        out[code] = {
            "stock_name": str(row[1]).strip(),
            "close_price": None,
            "dividend_yield": _parse_float(row[5]),
            "dividend_year": str(row[4]).strip() or None,
            "per": _parse_float(row[2]),
            "pbr": _parse_float(row[6]),
            "financial_report_period": None,
        }
    return out


async def _fetch_latest_trading_day(
    d: date, max_lookback_days: int = MAX_LOOKBACK_DAYS, market: str = "上市",
) -> tuple[Optional[date], Optional[dict[str, dict]]]:
    """從 d 往前找最近一個有資料的交易日。

    回傳：
      * `(data_date, payload)`：找到交易日（payload 非空）
      * `(None, {})`：回溯範圍內全部無資料
      * `(None, None)`：途中上游失敗（立即停止，不再往前打）
    """
    otc = market == "上櫃"
    min_date = TPEX_VALUATION_MIN_DATE if otc else VALUATION_MIN_DATE
    fetch = _fetch_tpex_pe_day if otc else _fetch_twse_bwibbu_day
    floor = max(min_date, d - timedelta(days=max(max_lookback_days, 0)))
    current = d
    while current >= floor:
        payload = await fetch(current)
        if payload is None:
            return None, None
        if payload:
            return current, payload
        current -= timedelta(days=1)
    return None, {}


_BWIBBU_FIELD_NAMES = {
    "close_price": "收盤價", "dividend_yield": "殖利率(%)", "dividend_year": "股利年度",
    "per": "本益比", "pbr": "股價淨值比", "financial_report_period": "財報年/季",
}
# 沒有 `fields` 時沿用近年 8 欄位置
_BWIBBU_DEFAULT_FIELDS = ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"]


def _parse_bwibbu_payload(data: dict) -> dict[str, dict]:
    """解析 BWIBBU_d JSON payload，回 stk_code -> row（依 `fields` 名稱對應欄位）。

    版面（2026-11-25 實測）：
      * 2017-04 中以前：5 欄 [證券代號, 證券名稱, 本益比, 殖利率(%), 股價淨值比]，沒有收盤價
        → close_price / dividend_year / financial_report_period 為 None
      * 之後：8 欄 [證券代號, 證券名稱, 收盤價, 殖利率(%), 股利年度, 本益比, 股價淨值比, 財報年/季]
    舊版固定要求 8 欄，2017-04 以前的交易日全部解析成空 dict 並被快取成「非交易日」（I010）。
    """
    if not isinstance(data, dict) or (data.get("stat") or "").upper() != "OK":
        return {}
    fields = [str(f).strip() for f in (data.get("fields") or _BWIBBU_DEFAULT_FIELDS)]
    index = {name: i for i, name in enumerate(fields)}
    if "本益比" not in index:
        return {}
    out: dict[str, dict] = {}
    for row in data.get("data") or []:
        if not row or len(row) < len(fields):
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        item: dict[str, Any] = {"stock_name": str(row[1]).strip()}
        for key, name in _BWIBBU_FIELD_NAMES.items():
            i = index.get(name)
            raw = row[i] if i is not None else None
            if key in ("dividend_year", "financial_report_period"):
                item[key] = (str(raw).strip() or None) if raw is not None else None
            else:
                item[key] = _parse_float(raw)
        out[code] = item
    return out


# =============================================================================
# 反推公式（每股層級）
# =============================================================================

def derive_per_share(
    close: Optional[float],
    per: Optional[float],
    pbr: Optional[float],
    yld: Optional[float],
) -> dict[str, Optional[float]]:
    """反推每股淨值 / 每股純益 / 每股股利。

    * BVPS = close / PBR      當 close, PBR > 0
    * EPS  = close / PER      當 close, PER > 0
    * DPS  = close * yld / 100 當 close, yld 已知；yld 可為 0

    close 缺 / <=0 → 三個都 None。
    """
    if close is None or close <= 0:
        return {"eps": None, "bvps": None, "dps": None}
    bvps = close / pbr if (pbr is not None and pbr > 0) else None
    eps = close / per if (per is not None and per > 0) else None
    dps = close * (yld / 100.0) if (yld is not None and yld >= 0) else None
    return {"eps": eps, "bvps": bvps, "dps": dps}


# =============================================================================
# 單股層級：反推每股資料 + 估市值 → constituent row
# =============================================================================

def build_constituent_row(
    code: str,
    row: dict,
    basic_entry: Optional[dict],
) -> tuple[Optional[dict], Optional[str]]:
    """由 (BWIBBU_d row + company_basic_info entry) 建構一筆 constituent。

    共用給 market aggregator（loop 用）與 by-company endpoint（單筆用）。

    * 股數 = paid_in_capital / PAR_VALUE(10)（台股面額慣例；估算值）
    * 三個指標 (PER / PBR / yield) 分別判斷是否可反推，缺哪個就 flag 該指標為 False

    回傳 `(row_dict, None)` 表示成功；
    回傳 `(None, reason)` 表示整筆排除，`reason ∈ {"no_price", "no_shares"}`。
    """
    close = row.get("close_price")
    per = row.get("per")
    pbr = row.get("pbr")
    yld = row.get("dividend_yield")

    b = basic_entry or {}
    pic = b.get("paid_in_capital")
    name = (b.get("short_name") or "").strip() or row.get("stock_name")

    if close is None or close <= 0:
        return None, "no_price"
    if not pic or pic <= 0:
        return None, "no_shares"

    shares = pic / PAR_VALUE
    mv = close * shares

    derived = derive_per_share(close, per, pbr, yld)
    eps = derived["eps"]
    bvps = derived["bvps"]
    dps = derived["dps"]

    return (
        {
            "stk_code": code,
            "stock_name": name,
            "close_price": close,
            "per": per,
            "pbr": pbr,
            "dividend_yield_pct": yld,
            "estimated_shares": shares,
            "market_cap": mv,
            "eps_ttm": eps,
            "bvps": bvps,
            "dps": dps,
            "included_in_per": eps is not None,
            "included_in_pbr": bvps is not None,
            "included_in_yield": dps is not None,
        },
        None,
    )


# =============================================================================
# 全市場加總 / 指標計算
# =============================================================================

def aggregate_market_summary(
    payload: dict[str, dict],
    basic: dict[str, dict],
    sample_size: int = 5,
) -> dict:
    """把 (per-day per-stock payload, company_basic_info) 加總成全市場指標。

    * 股數 = paid_in_capital / 10（面額慣例；標示為 estimated）
    * 三個指標分別維護各自的納入集合，缺哪個排除哪個

    回傳結構見 module docstring；由 endpoint 補上 date / market_scope / source 等 meta。
    """
    total_market_cap_per = 0.0
    total_market_cap_pbr = 0.0
    total_market_cap_yield = 0.0
    total_market_cap_all = 0.0  # 至少 close + shares 有效者
    total_net_income = 0.0
    total_book_value = 0.0
    total_cash_dividend = 0.0

    per_included = 0
    pbr_included = 0
    yield_included = 0
    excluded_no_shares = 0
    excluded_no_price = 0
    included_any = 0
    total_rows = len(payload)

    constituents: list[dict] = []

    for code, row in payload.items():
        c, reason = build_constituent_row(code, row, basic.get(code))
        if c is None:
            if reason == "no_price":
                excluded_no_price += 1
            elif reason == "no_shares":
                excluded_no_shares += 1
            continue

        mv = c["market_cap"]
        shares = c["estimated_shares"]
        eps = c["eps_ttm"]
        bvps = c["bvps"]
        dps = c["dps"]

        total_market_cap_all += mv
        included_any += 1

        if c["included_in_per"]:
            total_market_cap_per += mv
            total_net_income += eps * shares
            per_included += 1
        if c["included_in_pbr"]:
            total_market_cap_pbr += mv
            total_book_value += bvps * shares
            pbr_included += 1
        if c["included_in_yield"]:
            total_market_cap_yield += mv
            total_cash_dividend += dps * shares
            yield_included += 1

        if len(constituents) < sample_size:
            constituents.append(c)

    market_per = (
        total_market_cap_per / total_net_income
        if total_net_income > 0 else None
    )
    market_pbr = (
        total_market_cap_pbr / total_book_value
        if total_book_value > 0 else None
    )
    market_yield = (
        total_cash_dividend / total_market_cap_yield * 100.0
        if total_market_cap_yield > 0 else None
    )

    excluded_count = total_rows - included_any

    return {
        "total_market_cap": total_market_cap_all,
        "total_market_cap_per_basis": total_market_cap_per,
        "total_market_cap_pbr_basis": total_market_cap_pbr,
        "total_market_cap_yield_basis": total_market_cap_yield,
        "total_net_income": total_net_income,
        "total_book_value": total_book_value,
        "total_cash_dividend": total_cash_dividend,
        "market_per": market_per,
        "market_pbr": market_pbr,
        "market_dividend_yield_pct": market_yield,
        "total_rows": total_rows,
        "constituent_count": included_any,
        "per_included": per_included,
        "pbr_included": pbr_included,
        "yield_included": yield_included,
        "excluded_count": excluded_count,
        "excluded_no_price": excluded_no_price,
        "excluded_no_shares": excluded_no_shares,
        "sample_constituents": constituents,
    }


# =============================================================================
# 對外主函式
# =============================================================================

async def get_market_valuation_summary(d: date, sample_size: int = 5) -> dict:
    """取得 d 當日（或之前最近交易日）全市場 PER / 殖利率 / 股價淨值比彙總。

    * d 為非交易日 / 尚未收盤 → 回溯最近交易日，`data_date` 標示實際日期
    * 回溯範圍內都沒資料 → `found=False`，`reason="no_market_data"`（endpoint 回 404）
    * 上游失敗 → `found=False`，`reason="upstream_error"`（endpoint 回 502）
    """
    data_date, payload = await _fetch_latest_trading_day(d)
    base = {
        "date": d.isoformat(),
        "data_date": data_date.isoformat() if data_date else None,
        "market_scope": "TWSE (上市)",
        "calculation_method": _CALCULATION_METHOD,
        "source": _SOURCE,
    }
    if not payload:
        return {
            "found": False,
            **base,
            "reason": "upstream_error" if payload is None else "no_market_data",
            "summary": None,
        }

    basic = await load_basic_table()
    agg = aggregate_market_summary(payload, basic, sample_size=sample_size)
    return {"found": True, **base, "reason": None, "summary": agg}


# =============================================================================
# By-company 對外主函式
# =============================================================================

async def get_company_valuation(stock_id: str, d: date) -> dict:
    """取得單一上市 / 上櫃公司於 d 當日（或之前最近交易日）的估值單股明細。

    市場別取自 `company_basic_info`：上櫃走 TPEx peQryDate + dailyQuotes（2007-01-02 起，I005），
    其餘走 TWSE BWIBBU_d。回應格式相同，`source` 標示實際來源。

    * d 為非交易日 / 尚未收盤 → 回溯最近交易日，`data_date` 標示實際日期
    * 回溯範圍內都沒資料 → `found=False`，`reason="no_market_data"`（endpoint 回 404）
    * 上游失敗 → `found=False`，`reason="upstream_error"`（endpoint 回 502）
    * 股票代號不在該交易日 payload 中 → `found=False`，`reason="stock_id_not_listed"`
      （不再往前找：BWIBBU_d 每個交易日都列出全部上市股，當天沒有就代表不在涵蓋範圍）
    * 該股當天沒有收盤價（只有零股成交）→ 在回溯窗口內往前找該股有收盤價的交易日；
      找不到才回 `found=False`、`reason="no_price"`
    * 該交易日有資料但缺 shares → `found=False`，reason 對應雷同 market aggregator
    * 有完整資料 → `found=True`，`constituent` 含反推後的每股資料 + 估算股數 / 市值
    """
    code = (stock_id or "").strip()
    basic = await load_basic_table()
    market = (basic.get(code) or {}).get("market") or "上市"

    def _result(found: bool, reason: Optional[str], data_date: Optional[date] = None,
                constituent: Optional[dict] = None) -> dict:
        return {
            "found": found,
            "date": d.isoformat(),
            "data_date": data_date.isoformat() if data_date else None,
            "stock_id": code,
            "reason": reason,
            "calculation_method": _CALCULATION_METHOD,
            "source": _SOURCE_TPEX if market == "上櫃" else _SOURCE,
            "constituent": constituent,
        }

    if not code:
        return _result(False, "stock_id_not_listed")

    data_date, payload = await _fetch_latest_trading_day(d, market=market)
    if payload is None:
        return _result(False, "upstream_error")
    if not payload:
        return _result(False, "no_market_data")

    row = payload.get(code)
    if row is None:
        return _result(False, "stock_id_not_listed", data_date)

    c, reason = build_constituent_row(code, row, basic.get(code))
    # 當天沒有整股成交（只有零股）→ 收盤價「---」：在同一個回溯窗口內往前找這檔最近一個有收盤價的
    # 交易日，與「非交易日回溯」同語意（例：1264 德麥 2025-09-05 只有零股成交）。
    # 舊版 BWIBBU_d（2017-04 前）整張表都沒有收盤價欄，往前找也不會有 → 只在「別檔有收盤價」時回溯。
    floor = d - timedelta(days=MAX_LOOKBACK_DAYS)
    has_close_column = any((r or {}).get("close_price") is not None for r in payload.values())
    while (c is None and reason == "no_price" and has_close_column and data_date is not None
           and data_date - timedelta(days=1) >= floor):
        prev = data_date - timedelta(days=1)
        prev_date, prev_payload = await _fetch_latest_trading_day(prev, (prev - floor).days, market=market)
        if prev_payload is None:
            return _result(False, "upstream_error")
        if not prev_payload or code not in prev_payload:
            break
        data_date = prev_date
        c, reason = build_constituent_row(code, prev_payload[code], basic.get(code))
    if c is None:
        return _result(False, reason, data_date)
    return _result(True, None, data_date, c)


# =============================================================================
# 未來歷史補抓 helper（本次不做 DB 寫入；保留擴充空間）
# =============================================================================

def iter_month_dates(year: int, month: int) -> Iterator[date]:
    """產生指定年月的所有日期序列，供未來逐月歷史補抓使用。

    範例：
        async def backfill_month(year: int, month: int):
            for d in iter_month_dates(year, month):
                payload = await _fetch_twse_bwibbu_day(d)
                # ... 後續持久化步驟 ...
    """
    if not (2000 <= year <= 9999) or not (1 <= month <= 12):
        raise ValueError(f"invalid year/month: {year}/{month}")
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, 1)
    while d <= date(year, month, last_day):
        yield d
        d += timedelta(days=1)
