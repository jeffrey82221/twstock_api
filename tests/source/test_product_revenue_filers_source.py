"""`get_product_revenue_filers` must not turn a transient MOPS failure into a
cached "nobody filed" result (PR #80 CI: product_revenue_* returned 0 rows)."""
from __future__ import annotations

import asyncio

import httpx

import app.sources as src


def test_filer_list_failure_is_recorded_and_not_cached(monkeypatch):
    calls = {"n": 0}

    async def failing_post(self, url, **kwargs):
        calls["n"] += 1
        raise httpx.ConnectTimeout("timed out", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post)
    src._mops_filer_cache.pop(("10001", "sii"), None)

    async def run():
        src._request_source_errors.set([])
        first = await src.get_product_revenue_filers("10001", "sii")
        second = await src.get_product_revenue_filers("10001", "sii")
        return first, second, list(src._request_source_errors.get())

    first, second, errors = asyncio.run(run())
    assert first == [] and second == []
    assert calls["n"] == 2  # second call retried upstream instead of reading a cached empty list
    assert ("10001", "sii") not in src._mops_filer_cache
    assert len(errors) == 2 and all(e.source == "MOPS" for e in errors)


def test_terminal_redirect_is_classified_as_rate_limited():
    request = httpx.Request("GET", "https://www.twse.com.tw/fund/MI_QFIIS")
    response = httpx.Response(307, request=request, text="<html>block</html>")
    exc = httpx.HTTPStatusError("307", request=request, response=response)
    status, _msg, rate_limited = src._classify_http_exc(exc, str(request.url))
    assert status == 307 and rate_limited is True
