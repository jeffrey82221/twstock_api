"""MOPS IFRS quarterly operating analysis and EPS data source."""
from __future__ import annotations

import asyncio
import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

from .sources import SourceError, _classify_http_exc, _record_source_error, load_basic_table

MOPS_OPERATING_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb06"
MOPS_INCOME_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
QUARTERLY_MIN_DATE = date(2013, 3, 31)
CACHE_ROOT = Path("/tmp/valuation_cache/mops_quarterly")

_OPERATING_COLUMNS = (
    "stock_id", "company_name", "revenue_millions", "gross_margin_pct",
    "operating_margin_pct", "pretax_margin_pct", "net_margin_pct",
)
_INCOME_COLUMNS = (
    "stock_id", "company_name", "revenue", "cost", "operating_expenses",
    "operating_income", "non_operating_income_expense", "pretax_income",
    "income_tax", "continuing_net_income", "discontinued_net_income",
    "pre_control_net_income", "net_income", "other_comprehensive_income",
    "pre_control_comprehensive_income", "comprehensive_income",
    "parent_net_income", "common_control_net_income", "non_controlling_net_income",
    "parent_comprehensive_income", "common_control_comprehensive_income",
    "non_controlling_comprehensive_income", "eps",
)


def _parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("\u3000", "").strip()
    if text in {"", "-", "--", "—", "－", "N/A", "不適用"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_table_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[list[str]] = []
    for table in soup.find_all("table", class_="hasBorder"):
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if cells and cells[0].isdigit():
                rows.append(cells)
    return rows


def parse_operating_analysis_html(html: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for cells in _parse_table_rows(html):
        if len(cells) < len(_OPERATING_COLUMNS):
            continue
        row: dict[str, Any] = {"stock_id": cells[0], "company_name": cells[1]}
        for index, column in enumerate(_OPERATING_COLUMNS[2:], start=2):
            row[column] = _parse_number(cells[index])
        result[cells[0]] = row
    return result


def parse_income_statement_html(html: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table", class_="hasBorder"):
        table_rows = table.find_all("tr")
        if not table_rows:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in table_rows[0].find_all(["th", "td"])]
        eps_index = next((index for index, header in enumerate(headers) if "每股盈餘" in header), None)
        if eps_index is None:
            continue
        for tr in table_rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
            if len(cells) <= eps_index or not cells or not cells[0].isdigit():
                continue
            result[cells[0]] = {
                "stock_id": cells[0],
                "company_name": cells[1] if len(cells) > 1 else None,
                "eps": _parse_number(cells[eps_index]),
            }
    return result


def _quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return next_month.fromordinal(next_month.toordinal() - 1)


def _period_for(as_of: date) -> tuple[int, int, date]:
    if as_of < QUARTERLY_MIN_DATE:
        raise ValueError(f"as_of must be >= {QUARTERLY_MIN_DATE.isoformat()}")
    quarter = (as_of.month - 1) // 3 + 1
    year = as_of.year
    period_end = _quarter_end(year, quarter)
    if period_end > as_of:
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
        period_end = _quarter_end(year, quarter)
    return year - 1911, quarter, period_end


def _previous_period(year_tw: int, quarter: int) -> tuple[int, int, date]:
    year = year_tw + 1911
    if quarter == 1:
        year -= 1
        quarter = 4
    else:
        quarter -= 1
    return year - 1911, quarter, _quarter_end(year, quarter)


def _cache_path(kind: str, market: str, year_tw: int, quarter: int) -> Path:
    return CACHE_ROOT / f"{kind}_{market}_{year_tw}Q{quarter}.json.gz"


def _cache_read(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    temporary.replace(path)


async def _fetch(kind: str, market: str, year_tw: int, quarter: int) -> dict[str, dict[str, Any]]:
    path = _cache_path(kind, market, year_tw, quarter)
    cached = _cache_read(path)
    if cached is not None:
        return cached
    url = MOPS_OPERATING_URL if kind == "operating" else MOPS_INCOME_URL
    form = {"step": "1", "firstin": "1", "off": "1", "TYPEK": market, "year": str(year_tw), "season": str(quarter)}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Referer": "https://mopsov.twse.com.tw/mops/web/t163sb06",
    }
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            response = await client.post(url, data=form)
            response.raise_for_status()
            response.encoding = "utf-8"
            parser = parse_operating_analysis_html if kind == "operating" else parse_income_statement_html
            payload = parser(response.text)
    except Exception as exc:
        status, message, rate_limited = _classify_http_exc(exc, url)
        _record_source_error(SourceError(source="MOPS", url=url, status_code=status, message=message, is_rate_limited=rate_limited))
        return {}
    _cache_write(path, payload)
    return payload


async def get_quarterly_financials(stock_id: str, as_of: date) -> dict[str, Any]:
    year_tw, quarter, period_end = _period_for(as_of)
    stock = str(stock_id).strip()
    basic = await load_basic_table()
    basic_market = (basic.get(stock) or {}).get("market")
    markets = ["sii" if basic_market == "上市" else "otc"] if basic_market in {"上市", "上櫃"} else ["sii", "otc"]
    while period_end >= QUARTERLY_MIN_DATE:
        for market in markets:
            operating, income = await asyncio.gather(_fetch("operating", market, year_tw, quarter), _fetch("income", market, year_tw, quarter))
            if stock not in operating:
                continue
            op = operating[stock]
            inc = income.get(stock, {})
            return {
                "found": True, "stock_id": stock, "company_name": op.get("company_name") or inc.get("company_name"),
                "market": market, "as_of": as_of.isoformat(), "data_date": period_end.isoformat(),
                "fiscal_year": period_end.year, "quarter": quarter, "data": {**op, "eps": inc.get("eps")},
                "source": "MOPS t163sb06 + t163sb04",
            }
        year_tw, quarter, period_end = _previous_period(year_tw, quarter)
    return {"found": False, "stock_id": stock, "as_of": as_of.isoformat(), "data_date": period_end.isoformat(), "fiscal_year": period_end.year, "quarter": quarter, "data": None, "source": "MOPS t163sb06 + t163sb04"}