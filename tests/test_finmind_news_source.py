from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import finmind_news_source, icchain, main


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


def test_finmind_news_endpoint_returns_404_when_source_not_found(monkeypatch):
    async def fake_get_news(stock_id, as_of):
        return {"found": False, "data_date": None, "items": []}

    async def fake_ensure_loaded(*args, **kwargs):
        return None

    monkeypatch.setattr(finmind_news_source, "get_news", fake_get_news)
    monkeypatch.setattr(icchain, "ensure_loaded", fake_ensure_loaded)

    with TestClient(main.app) as client:
        response = client.get("/api/company/2330/news/finmind", params={"as_of": "2024-01-06"})

    assert response.status_code == 404
    assert response.json() == {"detail": "no FinMind news for stock_id='2330' on 2024-01-06"}