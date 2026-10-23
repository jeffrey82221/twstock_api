from datetime import date

import pytest

from app import finmind_news_source


@pytest.mark.anyio
async def test_get_news_queries_only_as_of_day(monkeypatch):
    calls = []

    async def fake_fetch_day(stock_id, day):
        calls.append((stock_id, day))
        return [{
            "date": "2024-01-08 09:00:00",
            "stock_id": stock_id,
            "title": "sample",
            "source": "source",
            "link": "https://example.com/news",
        }]

    monkeypatch.setattr(finmind_news_source, "_fetch_day", fake_fetch_day)
    result = await finmind_news_source.get_news("2330", date(2024, 1, 8))

    assert result["data_date"] == "2024-01-08"
    assert len(calls) == 1
    assert calls[0] == ("2330", date(2024, 1, 8))


@pytest.mark.anyio
async def test_get_news_returns_not_found_without_fallback(monkeypatch):
    calls = []

    async def fake_fetch_day(stock_id, day):
        calls.append(day)
        return []

    monkeypatch.setattr(finmind_news_source, "_fetch_day", fake_fetch_day)
    result = await finmind_news_source.get_news("2330", date(2024, 1, 6))

    assert result["found"] is False
    assert result["data_date"] is None
    assert calls == [date(2024, 1, 6)]