#!/usr/bin/env python3
"""Generate an interactive LineageX graph for this repository's poc SQL.

The poc files are SELECT bodies with Jinja ``{{ schema }}`` placeholders, while
LineageX expects named SQL objects. This tool renders every body as
``CREATE VIEW poc.<file_stem> AS ...`` in a temporary directory, then runs
LineageX in offline mode. The generated ``index.html`` is self-contained apart
from its adjacent ``app.js`` / ``vendor.js`` assets.

Examples:
    python tools/render_lineagex.py
    python tools/render_lineagex.py --serve --port 5200
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import webbrowser
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jinja2 import Template

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_DIR = ROOT / "db" / "poc"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "lineagex"


def strip_full_line_comments(sql: str) -> str:
    """Remove comments before LineageX reads the temporary SQL as Latin-1.

    The production SQL keeps Chinese design comments, but LineageX 0.0.27
    reads SQL files with Latin-1. Removing only full-line comments preserves
    the executable query and prevents encoding corruption from reaching its
    SQL parser.
    """
    return re.sub(r"(?m)^\s*--.*$", "", sql)


def render_poc_sql(source_dir: Path, destination_dir: Path, schema: str) -> list[str]:
    """Render poc SELECT bodies into named CREATE VIEW statements for LineageX."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    view_names: list[str] = []
    for source_path in sorted(source_dir.glob("*.sql")):
        view_name = source_path.stem
        body = Template(source_path.read_text(encoding="utf-8")).render(schema=schema)
        body = strip_full_line_comments(body).strip().removesuffix(";")
        rendered = f"CREATE VIEW {schema}.{view_name} AS\n{body};\n"
        (destination_dir / source_path.name).write_text(rendered, encoding="utf-8")
        view_names.append(view_name)
    return view_names


def build_lineage(sql_dir: Path, output_dir: Path, schema: str) -> dict:
    """Render the project's SQL and write LineageX's graph assets to output_dir."""
    if not sql_dir.is_dir():
        raise ValueError(f"SQL directory does not exist: {sql_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    for asset_name in ("index.html", "output.json", "app.js", "vendor.js", "manifest.json"):
        (output_dir / asset_name).unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="lineagex-sql-") as temp_dir:
        views = render_poc_sql(sql_dir, Path(temp_dir), schema)
        previous_cwd = Path.cwd()
        try:
            os.chdir(output_dir)
            from lineagex.lineagex import lineagex

            lineagex(
                sql=temp_dir,
                target_schema=schema,
                search_path_schema=schema,
                dialect="postgres",
            )
        finally:
            os.chdir(previous_cwd)

    required_assets = [output_dir / name for name in ("index.html", "output.json", "app.js", "vendor.js")]
    missing_assets = [str(path) for path in required_assets if not path.exists()]
    if missing_assets:
        raise RuntimeError(f"LineageX did not create required output assets: {missing_assets}")

    lineage = json.loads((output_dir / "output.json").read_text(encoding="utf-8"))
    manifest = {
        "generator": "tools/render_lineagex.py",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_dir": str(sql_dir),
        "schema": schema,
        "rendered_view_count": len(views),
        "lineage_object_count": len(lineage),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def serve(output_dir: Path, port: int, open_browser: bool) -> None:
    """Serve generated static LineageX assets until interrupted."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(output_dir))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Serving LineageX at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLineageX server stopped.")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render db/poc lineage with LineageX.")
    parser.add_argument("--sql-dir", type=Path, default=DEFAULT_SQL_DIR, help="SQL directory to parse.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Generated LineageX assets directory.")
    parser.add_argument("--schema", default="poc", help="Schema name used when rendering Jinja SQL.")
    parser.add_argument("--serve", action="store_true", help="Serve generated output after rendering.")
    parser.add_argument("--port", type=int, default=5200, help="Port used with --serve.")
    parser.add_argument("--open", action="store_true", help="Open the served graph in the default browser.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = build_lineage(args.sql_dir.resolve(), args.output_dir.resolve(), args.schema)
    except Exception as exc:  # noqa: BLE001 - CLI should print a concise failure
        print(f"LineageX rendering failed: {exc}", file=sys.stderr)
        return 1

    print(
        "LineageX graph generated: "
        f"{manifest['lineage_object_count']} lineage objects from "
        f"{manifest['rendered_view_count']} poc SQL files at {args.output_dir.resolve() / 'index.html'}"
    )
    if args.serve:
        serve(args.output_dir.resolve(), args.port, args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
