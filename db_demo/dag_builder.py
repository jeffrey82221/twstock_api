"""Builds a dependency-graph description of db/poc/*.sql views for the DB demo app.

Reuses ``Pipeline``'s already-computed dependency data (``src_tables`` /
``seed_tables``) instead of re-parsing SQL, so the graph always matches what
``Pipeline.create_views()`` actually executes.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, List

from pipeline import Pipeline


def build_dag(pipeline: Pipeline) -> Dict[str, list]:
    """Return ``{'nodes': [...], 'edges': [...]}`` for the poc SQL dependency graph."""
    seed_names = set(pipeline.seed_tables)
    nodes = [
        {
            "id": sql_path[: -len(".sql")],
            "is_seed": sql_path[: -len(".sql")] in seed_names,
            "is_raw": sql_path.startswith("raw_"),
        }
        for sql_path in pipeline._sql_paths
    ]

    edges = []
    for sql_path, upstream_tables in pipeline.src_tables.items():
        dest = sql_path[: -len(".sql")]
        for upstream_table in upstream_tables:
            src = upstream_table.split(".")[-1]
            edges.append({"from": src, "to": dest})

    _assign_layers(nodes, edges)
    return {"nodes": nodes, "edges": edges}


def _assign_layers(nodes: List[dict], edges: List[dict]) -> None:
    """Topologically layer nodes (longest-path-from-root) for left-to-right rendering."""
    ids = [n["id"] for n in nodes]
    indeg = {i: 0 for i in ids}
    adjacency: Dict[str, List[str]] = {i: [] for i in ids}
    for edge in edges:
        if edge["from"] in adjacency and edge["to"] in indeg:
            adjacency[edge["from"]].append(edge["to"])
            indeg[edge["to"]] += 1

    layer = {i: 0 for i in ids}
    remaining = dict(indeg)
    queue = deque([i for i in ids if indeg[i] == 0])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            layer[neighbor] = max(layer[neighbor], layer[current] + 1)
            remaining[neighbor] -= 1
            if remaining[neighbor] == 0:
                queue.append(neighbor)

    for node in nodes:
        node["layer"] = layer[node["id"]]
