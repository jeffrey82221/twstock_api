"""準確性抽查／診斷：繞過 adapter 的解析，直接印出上游頁面的原始內容（README §9.1、指令 3 抽查）。

用法（repo 根目錄）：
    .venv/bin/python verify_questions/upstream_peek.py t05st08 1519:11206 2882:11510   # MOPS 產品營收表的原始儲存格
    .venv/bin/python verify_questions/upstream_peek.py t21sc03 sii:115:9 sii:115:8      # MOPS 月營收彙總頁的完整度

t05st08 每個參數打 2 次 MOPS（申報清單＋明細），t21sc03 每個參數 1 次，算進 §8.3 的 MOPS 預算。
只用到 app/sources.py 的 URL 常數與 HTTP 標頭，不經過 _parse_mops_table，所以看得到 adapter 丟掉的列。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import sources as s  # noqa: E402

_CELL = re.compile(r"<T[DH][^>]*>(.*?)</T[DH]>", re.I | re.S)


def _cells(row: str) -> list[str]:
    return [re.sub(r"<[^>]+>|&nbsp;", "", x).strip() for x in _CELL.findall(row)]


async def t05st08(pairs: list[str]) -> None:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=s._MOPS_HEADERS) as c:
        for pair in pairs:
            stock, ym = pair.split(":")
            for typek in ("sii", "otc"):
                ids, ff, url = await s._fetch_filer_list(c, ym, typek)
                if stock in ids:
                    break
            else:
                print(f"== {stock} {ym}: 不在 sii／otc 申報清單")
                continue
            payload = {**ff, "co_id": stock}
            payload.setdefault("encodeURIComponent", "1")
            r = await c.post(s.MOPS_T05ST08_URL, data=payload, headers={
                **s._MOPS_HEADERS, "Content-Type": "application/x-www-form-urlencoded",
                "Referer": url or s.MOPS_T05ST08_URL, "Origin": "https://mopsov.twse.com.tw"})
            r.encoding = r.encoding or "utf-8"
            t = re.search(r"<TABLE[^>]*class=['\"]hasBorder['\"][^>]*>(.*?)</TABLE>", r.text, re.I | re.S)
            print(f"== {stock} {ym} ({typek})")
            for row in re.findall(r"<TR[^>]*>(.*?)</TR>", t.group(1) if t else "", re.I | re.S):
                cells = _cells(row)
                if any(cells[1:]) or len(cells) == 1:
                    print("  ", cells)


def t21sc03(pages: list[str]) -> None:
    for page in pages:
        kind, roc_year, month = page.split(":")
        url = f"{s.MOPS_HISTORY_BASE}/{kind}/t21sc03_{roc_year}_{month}_0.html"
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, follow_redirects=True)
        text = r.content.decode("big5", errors="replace")
        rows = s._parse_mops_revenue_html(text) if r.status_code == 200 else {}
        industries = re.findall(r"產業別：([^<]+)<", text)
        complete = text.rstrip().lower().endswith("</html>")
        print(f"== {url}\n   HTTP {r.status_code}  {len(r.content):,} bytes  公司 {len(rows)}  產業段 {len(industries)}"
              f"（{industries[0] if industries else '-'} … {industries[-1] if industries else '-'}）"
              f"  結尾完整 {complete}  Last-Modified {r.headers.get('last-modified')}")
        if not complete:
            print(f"   結尾：{text[-120:]!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["t05st08", "t21sc03"])
    ap.add_argument("args", nargs="+")
    a = ap.parse_args()
    if a.kind == "t05st08":
        asyncio.run(t05st08(a.args))
    else:
        t21sc03(a.args)


if __name__ == "__main__":
    main()
