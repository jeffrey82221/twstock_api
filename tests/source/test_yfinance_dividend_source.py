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
