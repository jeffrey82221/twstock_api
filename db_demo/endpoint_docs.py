"""Maps db/poc raw_*.sql views to the app/main.py API endpoint(s) they call.

Lets the DB demo UI show, for any view that calls ``custom.http_get_content``,
which ``app/main.py`` endpoint it actually hits plus that endpoint's OpenAPI
summary/description — so a table's origin (and the underlying "投資判斷資料源")
can be traced back without reading the SQL comments by hand.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional

MAIN_API_BASE_URL = os.environ.get("TWSTOCK_MAIN_API_BASE_URL", "http://127.0.0.1:5002")
_POC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "poc")
_HTTP_CALL_MARKER = "custom.http_get_content("
_TOKEN_RE = re.compile(r"'([^']*)'|([^']+)")
_QUOTED_RE = re.compile(r"'([^']*)'")
_ALIAS_RE = re.compile(r"^\s*(?:::\w+)?\s*\)?\s*AS\s+(\w+)", re.IGNORECASE)

_openapi_cache: Optional[dict] = None


def _get_openapi_spec() -> dict:
    """Fetch + cache the running main API's OpenAPI spec (best-effort; never raises)."""
    global _openapi_cache
    if _openapi_cache is not None:
        return _openapi_cache
    try:
        with urllib.request.urlopen(f"{MAIN_API_BASE_URL}/openapi.json", timeout=3) as resp:
            _openapi_cache = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        _openapi_cache = {"paths": {}}
    return _openapi_cache


def _path_skeleton(path: str) -> str:
    """Normalize an OpenAPI path template or a reconstructed URL into a
    comparable skeleton: drop query string, collapse path params / any
    interpolated SQL expression to a single '*'.
    """
    path = path.split("?", 1)[0]
    path = re.sub(r"\{[^}]+\}", "*", path)
    path = path.replace("http://host.docker.internal:5002", "")
    return path.rstrip("/")


def _skeleton_from_expr(expr: str) -> str:
    """Reconstruct a position-accurate path skeleton from a SQL string-concat
    expression: quoted literals are kept verbatim, and every interpolated
    (non-literal) chunk becomes a single '*' in place — so
    ``'.../chain/' || ic_code`` becomes ``.../chain/*`` (not ``.../chain``).
    """
    parts = []
    for m in _TOKEN_RE.finditer(expr):
        literal, other = m.group(1), m.group(2)
        if literal is not None:
            parts.append(literal)
        else:
            # Drop trailing/leading type casts (e.g. "::TEXT") before deciding
            # whether this chunk represents real interpolated content.
            cleaned = re.sub(r"::\w+", "", other).strip(" \t\n|()")
            if cleaned:
                parts.append("*")
    return "".join(parts)


def _find_call_bodies(sql_text: str) -> List[str]:
    """Return the balanced-paren argument text of every http_get_content(...) call."""
    bodies = []
    start = 0
    while True:
        idx = sql_text.find(_HTTP_CALL_MARKER, start)
        if idx == -1:
            break
        i = idx + len(_HTTP_CALL_MARKER)
        depth = 1
        arg_start = i
        while i < len(sql_text) and depth > 0:
            if sql_text[i] == "(":
                depth += 1
            elif sql_text[i] == ")":
                depth -= 1
            i += 1
        bodies.append((sql_text[arg_start : i - 1], i))
        start = i
    return bodies


def _skeleton_for_call(sql_text: str, body: str) -> str:
    skeleton = _skeleton_from_expr(body)
    if skeleton:
        return skeleton
    # Body is a bare identifier (e.g. `custom.http_get_content(url)`) referencing
    # an earlier "(...) AS <identifier>" column in the same SELECT list.
    arg_name = body.strip()
    if re.fullmatch(r"\w+", arg_name):
        m = re.search(
            r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*AS\s+" + re.escape(arg_name) + r"\b",
            sql_text,
            re.DOTALL,
        )
        if m:
            return _skeleton_from_expr(m.group(1))
    return ""


def _extract_endpoint_skeletons(sql_text: str) -> List[Dict[str, Optional[str]]]:
    """Return [{'alias': str|None, 'path_skeleton': str}] for every upstream call."""
    calls = []
    for body, end_idx in _find_call_bodies(sql_text):
        skeleton = _path_skeleton(_skeleton_for_call(sql_text, body))
        if not skeleton.startswith("/api"):
            continue
        alias_match = _ALIAS_RE.match(sql_text[end_idx : end_idx + 60])
        calls.append({"alias": alias_match.group(1) if alias_match else None, "path_skeleton": skeleton})
    return calls


def get_endpoint_docs(view_name: str) -> List[Dict[str, Optional[str]]]:
    """Best-effort list of app/main.py endpoint(s) that ``db/poc/<view_name>.sql`` calls.

    Empty when the view doesn't call ``custom.http_get_content`` (normalized /
    _list views) or when no OpenAPI match / main API is unreachable.
    """
    sql_path = os.path.join(_POC_DIR, f"{view_name}.sql")
    if not os.path.exists(sql_path):
        return []
    with open(sql_path, "r") as f:
        sql_text = f.read()

    calls = _extract_endpoint_skeletons(sql_text)
    if not calls:
        return []

    spec = _get_openapi_spec()
    path_index: Dict[str, Dict[str, dict]] = {}
    for raw_path, methods in spec.get("paths", {}).items():
        path_index[_path_skeleton(raw_path)] = {"path": raw_path, "methods": methods}

    results = []
    seen_paths = set()
    for call in calls:
        entry = path_index.get(call["path_skeleton"])
        if entry is None or entry["path"] in seen_paths:
            continue
        seen_paths.add(entry["path"])
        method_name, op = next(iter(entry["methods"].items()))
        if "get" in entry["methods"]:
            method_name, op = "get", entry["methods"]["get"]
        results.append(
            {
                "alias": call["alias"],
                "method": method_name.upper(),
                "path": entry["path"],
                "summary": op.get("summary"),
                "description": op.get("description"),
                "tags": op.get("tags", []),
            }
        )
    return results
