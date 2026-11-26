"""yfinance 股利：還原 Yahoo 依配股（split）做的歷史調整（I003）。"""
import sys
import types

import pandas as pd

import app.yfinance_source as yfs


def test_factor_uses_only_splits_after_ex_date():
    splits = [("2020-08-12", 1.050016), ("2022-07-21", 1.0999282)]
    f = yfs.split_unadjust_factors(["2019-08-01", "2021-08-12", "2022-07-21", "2023-07-11"], splits)
    assert f[0] == 1.050016 * 1.0999282            # before both splits
    assert f[1] == 1.0999282                       # only the later split
    assert f[2] == 1.0 and f[3] == 1.0             # same-day split does not count


def test_dividends_are_unadjusted_to_actual_cash(monkeypatch):
    # 1101: Yahoo reports 3.0621 for 2021-08-12; FinMind / actual payout is 3.368.
    tz = "Asia/Taipei"
    fake = types.SimpleNamespace(
        dividends=pd.Series([3.0621, 0.9993], index=pd.to_datetime(["2021-08-12", "2022-07-21"]).tz_localize(tz)),
        splits=pd.Series([1.0999282], index=pd.to_datetime(["2022-07-21"]).tz_localize(tz)),
    )
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda sym: fake))
    rows = yfs._fetch_yfinance_dividends_sync("1101.TW")
    by_date = {r["date"]: r for r in rows}
    assert abs(by_date["2021-08-12"]["CashEarningsDistribution"] - 3.368) < 0.001
    assert by_date["2021-08-12"]["SplitUnadjustFactor"] == 1.0999282
    assert by_date["2022-07-21"]["CashEarningsDistribution"] == 0.9993


def test_no_splits_leaves_values_unchanged(monkeypatch):
    fake = types.SimpleNamespace(
        dividends=pd.Series([2.4614], index=pd.to_datetime(["2021-07-26"]).tz_localize("Asia/Taipei")),
        splits=pd.Series([], dtype=float),
    )
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda sym: fake))
    (row,) = yfs._fetch_yfinance_dividends_sync("2882.TW")
    assert row["CashEarningsDistribution"] == 2.4614 and row["SplitUnadjustFactor"] == 1.0


# ---- empty dividends: no-dividend vs failure (I014) ----
import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def yf_env(monkeypatch):
    yfs._yf_dividend_cache.clear()
    errors = []
    monkeypatch.setattr(yfs, "_record_source_error", lambda e: errors.append(e))
    monkeypatch.setattr(yfs, "_fetch_yfinance_dividends_sync", lambda sym: [])
    yield monkeypatch, errors
    yfs._yf_dividend_cache.clear()


def test_empty_dividends_with_prices_is_no_dividend_not_error(yf_env):
    mp, errors = yf_env
    mp.setattr(yfs, "_yf_has_prices_sync", lambda sym: True)
    assert asyncio.run(yfs.get_dividend_yf("1443", "上市")) == []
    assert errors == []
    assert yfs._yf_dividend_cache.get("1443.TW") == []        # explicit answer: cached


def test_empty_dividends_without_prices_is_error_and_not_cached(yf_env):
    mp, errors = yf_env
    mp.setattr(yfs, "_yf_has_prices_sync", lambda sym: False)
    assert asyncio.run(yfs.get_dividend_yf("9999", "上市")) == []
    assert len(errors) == 1 and "no price history" in errors[0].message
    assert yfs._yf_dividend_cache.get("9999.TW") is None      # retried next time


def test_empty_statements_with_prices_is_not_error(monkeypatch):
    yfs._yf_cache.clear()
    errors = []
    monkeypatch.setattr(yfs, "_record_source_error", lambda e: errors.append(e))
    monkeypatch.setattr(yfs, "_fetch_yfinance_sync", lambda sym: [])
    monkeypatch.setattr(yfs, "_yf_has_prices_sync", lambda sym: True)
    assert asyncio.run(yfs.get_financial_statements_yf("6913", "上櫃")) == []
    assert errors == [] and yfs._yf_cache.get("6913.TWO") == []
    monkeypatch.setattr(yfs, "_yf_has_prices_sync", lambda sym: False)
    assert asyncio.run(yfs.get_financial_statements_yf("9999", "上市")) == []
    assert len(errors) == 1 and yfs._yf_cache.get("9999.TW") is None
    yfs._yf_cache.clear()
