"""執行問題卡的決策 SQL 與斷言（指令 2 驗證 SQL、指令 3／6 判定結果）。

用法（repo 根目錄）：
    .venv/bin/python verify_questions/run_checks.py Q001            # 一題
    .venv/bin/python verify_questions/run_checks.py --all           # 全部問題卡
    .venv/bin/python verify_questions/run_checks.py --status passing # 只跑某狀態（指令 6）
    加 --json 輸出機器可讀結果；加 --decision 一併印出決策 SQL 的結果。

問題卡格式見 README §6.1：
- 「## 決策 SQL」下的第一個 ```sql 區塊是決策 SQL。
- 「## 斷言」下每個「### Cn …」標題底下第一個 ```sql 區塊回傳違規列；
  若之後還有一個 ```sql 區塊（前面文字含「可比對列數」），它回傳一個數字，
  = 0 時結果為 INCONCLUSIVE。
SQL 經 server.select 執行（與 MCP 同一個白名單與 LIMIT 包裝），執行前去掉 -- 註解。
violations 是完整違規列數（超過取樣上限時另跑 COUNT(*)），sample 只是前幾列。
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

_SQL_BLOCK = re.compile(r"```sql\n(.*?)```", re.DOTALL)

# 斷言標籤 → DAMA 維度（README §7.1）。標題的第一個字是標籤；不在表內的印成「?」，代表要改標籤。
TAG_DIMENSION = {
    "completeness": "完整性", "field": "完整性",
    "uniqueness": "唯一性",
    "point-in-time": "及時性", "timeliness": "及時性", "freshness": "及時性",
    "validity": "有效性",
    "accuracy": "準確性",
    "internal": "一致性", "cross-source": "一致性", "temporal": "一致性", "unit": "一致性",
    "precision": "精確性",
    "availability": "可取得性",
    "reasonableness": "合理性",
}


def dimension_of(title: str) -> str:
    tag = title.split()[0] if title.split() else ""
    return TAG_DIMENSION.get(tag, "?")


def _server():
    with contextlib.redirect_stdout(sys.stderr):
        import server
    return server


def _clean(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql).strip()


def parse_card(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    front = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    meta = {}
    if front:
        for line in front.group(1).splitlines():
            m = re.match(r"^(\w+):\s*(.*?)\s*(#.*)?$", line)
            if m:
                meta[m.group(1)] = m.group(2)
    decision = None
    sec = re.search(r"^## 決策 SQL\n(.*?)(?=^## )", text, re.DOTALL | re.MULTILINE)
    if sec and (b := _SQL_BLOCK.search(sec.group(1))):
        decision = b.group(1)
    checks = []
    sec = re.search(r"^## 斷言.*?\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    if sec:
        for m in re.finditer(r"^### (C\d+)\s*(.*?)\n(.*?)(?=^### |\Z)", sec.group(1), re.DOTALL | re.MULTILINE):
            blocks = list(_SQL_BLOCK.finditer(m.group(3)))
            if not blocks:
                continue
            comparable = None
            if len(blocks) > 1 and "可比對列數" in m.group(3)[blocks[0].end():blocks[1].start()]:
                comparable = blocks[1].group(1)
            checks.append({"id": m.group(1), "title": m.group(2).strip(),
                           "sql": blocks[0].group(1), "comparable_sql": comparable})
    return {"path": path, "meta": meta, "decision": decision, "checks": checks}


# server.select 會在查詢外包 LIMIT：取樣只拿這麼多列，違規數另用 COUNT(*) 查完整數字。
SAMPLE_LIMIT = 200


def count_violations(srv, sql: str, sampled: int) -> int:
    """完整違規列數。取樣未達上限時取樣數就是全部；達上限才另跑 COUNT(*)，避免數字卡在 200。"""
    if sampled < SAMPLE_LIMIT:
        return sampled
    (row,) = srv.select(f"SELECT count(*) AS n FROM ({sql}) AS violations", limit=1)
    return int(row["n"])


def run_card(card: dict, with_decision: bool = False) -> dict:
    srv = _server()
    out = {"id": card["meta"].get("id"), "title": card["meta"].get("title"), "checks": []}
    if with_decision and card["decision"]:
        try:
            out["decision"] = srv.select(_clean(card["decision"]))
        except Exception as e:  # report, keep going
            out["decision_error"] = f"{type(e).__name__}: {e}"
    for c in card["checks"]:
        r = {"id": c["id"], "title": c["title"], "dimension": dimension_of(c["title"])}
        try:
            sql = _clean(c["sql"])
            rows = srv.select(sql, limit=SAMPLE_LIMIT)
            r["violations"] = count_violations(srv, sql, len(rows))
            r["sample"] = rows[:5]
            if c["comparable_sql"]:
                first = srv.select(_clean(c["comparable_sql"]))[0]
                r["comparable"] = int(next(iter(first.values())))
            if r.get("comparable") == 0:
                r["result"] = "INCONCLUSIVE"
            else:
                r["result"] = "PASS" if not rows else "FAIL"
        except Exception as e:
            r["result"] = "ERROR"
            r["error"] = f"{type(e).__name__}: {e}"
        out["checks"].append(r)
    return out


def ledger_mismatches(cards: list[dict]) -> list[str]:
    """問題卡的 status 與 LEDGER「問題」表不一致的題目。

    兩邊不同步時，`--status passing`（指令 6 回歸）會漏掉題目：Q002 在 2026-11-26 修好後
    LEDGER 寫 passing、問題卡仍是 failing，回歸一直沒跑到它（第 9 輪才發現）。"""
    path = os.path.join(HERE, "LEDGER.md")
    if not os.path.exists(path):
        return []
    ledger = {}
    for m in re.finditer(r"^\| \[(Q\d+)\]\([^)]*\) \| [^|]* \| [^|]* \| ([a-z]+)", open(path, encoding="utf-8").read(), re.M):
        ledger[m.group(1)] = m.group(2)
    out = []
    for c in cards:
        qid, status = c["meta"].get("id"), c["meta"].get("status")
        if qid in ledger and ledger[qid] != status:
            out.append(f"{qid}: 問題卡 status={status}，LEDGER={ledger[qid]}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--decision", action="store_true")
    args = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(HERE, "questions", "Q*.md")))
    cards = [parse_card(p) for p in paths]
    for w in ledger_mismatches(cards):
        print(f"WARN status 不同步 {w}", file=sys.stderr)
    if not args.all:
        if args.status:
            cards = [c for c in cards if c["meta"].get("status") == args.status]
        if args.ids:
            cards = [c for c in cards if c["meta"].get("id") in args.ids]
    results = [run_card(c, with_decision=args.decision) for c in cards]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
        return
    for res in results:
        print(f"## {res['id']} {res['title']}")
        if "decision" in res:
            for row in res["decision"]:
                print("   decision:", json.dumps(row, ensure_ascii=False, default=str))
        if "decision_error" in res:
            print("   decision ERROR:", res["decision_error"])
        for c in res["checks"]:
            extra = f" comparable={c['comparable']}" if "comparable" in c else ""
            detail = c.get("error") or (f"violations={c['violations']}{extra}")
            print(f"   {c['id']} {c['result']:12s} [{c['dimension']}] {detail}  — {c['title']}")
            if c["result"] == "FAIL":
                for s in c["sample"][:3]:
                    print("        e.g.", json.dumps(s, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
