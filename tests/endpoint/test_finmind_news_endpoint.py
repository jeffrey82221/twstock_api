from fastapi.testclient import TestClient

from app import finmind_news_source, icchain, main


def test_finmind_news_endpoint_returns_404_when_source_not_found(monkeypatch):
    async def fake_get_news(stock_id, as_of):
        return {"found": False, "data_date": None, "items": []}

    async def fake_ensure_loaded(*args, **kwargs):
        return None

    monkeypatch.setattr(finmind_news_source, "get_news", fake_get_news)
    monkeypatch.setattr(icchain, "ensure_loaded", fake_ensure_loaded)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/company/2330/news/finmind",
            params={"as_of": "2024-01-06"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "no FinMind news for stock_id='2330' on 2024-01-06"
    }
