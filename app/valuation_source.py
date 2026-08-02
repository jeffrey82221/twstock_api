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
非交易日 / 上游空 payload 也 cache 空 dict，避免重打。

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
    _record_source_error,
    load_basic_table,
)

logger = logging.getLogger("twstock_api.valuation")

CACHE_ROOT = Path("/tmp/valuation_cache")

# BWIBBU_d 官方最早提供日：民國 94/9/2 = 2005-09-02（實測 2005 早期部分日期無資料，
# 但 2012 起穩定；保守取 2005-09-02 為下限）
VALUATION_MIN_DATE = date(2005, 9, 2)

TWSE_BWIBBU_URL = "https://www.twse.com.tw/exchangeReport/BWIBBU_d"

# 台股面額慣例：10 元 / 股（絕大多數上市普通股）
PAR_VALUE = 10.0


# =============================================================================
# 磁碟 cache
# =============================================================================

def _cache_path(d: date) -> Path:
    return CACHE_ROOT / "twse" / f"{d.strftime('%Y%m%d')}.json.gz"


def _cache_read(d: date) -> Optional[dict]:
    p = _cache_path(d)
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


def _cache_write(d: date, payload: dict) -> None:
    p = _cache_path(d)
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

async def _fetch_twse_bwibbu_day(d: date) -> dict[str, dict]:
    """回傳 stk_code -> row dict（該日全上市股票 PER / PBR / 殖利率 / 收盤價）。

    cache miss 時打上游、寫磁碟 cache；非交易日 / 空 payload 回 {} 也 cache。
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

    parsed = _parse_bwibbu_payload(data)
    _cache_write(d, parsed)
    return parsed


def _parse_bwibbu_payload(data: dict) -> dict[str, dict]:
    """解析 BWIBBU_d JSON payload，回 stk_code -> row。

    payload row 8 欄（TWSE fields 定義順序）：
      [0]證券代號 [1]證券名稱 [2]收盤價 [3]殖利率(%)
      [4]股利年度 [5]本益比 [6]股價淨值比 [7]財報年/季
    """
    if not isinstance(data, dict) or (data.get("stat") or "").upper() != "OK":
        return {}
    rows = data.get("data") or []
    out: dict[str, dict] = {}
    for row in rows:
        if not row or len(row) < 8:
            continue
        code = str(row[0]).strip()
        if not code:
            continue
        out[code] = {
            "stock_name": str(row[1]).strip(),
            "close_price": _parse_float(row[2]),
            "dividend_yield": _parse_float(row[3]),
            "dividend_year": str(row[4]).strip() or None,
            "per": _parse_float(row[5]),
            "pbr": _parse_float(row[6]),
            "financial_report_period": str(row[7]).strip() or None,
        }
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
        close = row.get("close_price")
        per = row.get("per")
        pbr = row.get("pbr")
        yld = row.get("dividend_yield")

        b = basic.get(code) or {}
        pic = b.get("paid_in_capital")
        name = (b.get("short_name") or "").strip() or row.get("stock_name")

        if close is None or close <= 0:
            excluded_no_price += 1
            continue
        if not pic or pic <= 0:
            excluded_no_shares += 1
            continue

        shares = pic / PAR_VALUE
        mv = close * shares
        total_market_cap_all += mv
        included_any += 1

        derived = derive_per_share(close, per, pbr, yld)
        eps = derived["eps"]
        bvps = derived["bvps"]
        dps = derived["dps"]

        included_in_per = eps is not None
        included_in_pbr = bvps is not None
        included_in_yield = dps is not None

        if included_in_per:
            total_market_cap_per += mv
            total_net_income += eps * shares
            per_included += 1
        if included_in_pbr:
            total_market_cap_pbr += mv
            total_book_value += bvps * shares
            pbr_included += 1
        if included_in_yield:
            total_market_cap_yield += mv
            total_cash_dividend += dps * shares
            yield_included += 1

        if len(constituents) < sample_size:
            constituents.append({
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
                "included_in_per": included_in_per,
                "included_in_pbr": included_in_pbr,
                "included_in_yield": included_in_yield,
            })

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
    """取得指定交易日全市場 PER / 殖利率 / 股價淨值比彙總。

    * payload 為空（非交易日 / 當日尚未收盤 / 上游失敗）
      → `found=False`（依 repo 慣例，讓 endpoint 回 404）
    """
    payload = await _fetch_twse_bwibbu_day(d)
    if not payload:
        return {
            "found": False,
            "date": d.isoformat(),
            "market_scope": "TWSE (上市)",
            "calculation_method": (
                "estimated_market_cap_weighted "
                "(shares = paid_in_capital / 10; par-value convention)"
            ),
            "source": "TWSE BWIBBU_d (per-day)",
            "summary": None,
        }

    basic = await load_basic_table()
    agg = aggregate_market_summary(payload, basic, sample_size=sample_size)

    return {
        "found": True,
        "date": d.isoformat(),
        "market_scope": "TWSE (上市)",
        "calculation_method": (
            "estimated_market_cap_weighted "
            "(shares = paid_in_capital / 10; par-value convention)"
        ),
        "source": "TWSE BWIBBU_d (per-day)",
        "summary": agg,
    }


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
