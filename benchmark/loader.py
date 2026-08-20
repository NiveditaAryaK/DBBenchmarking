"""Batched data loading + ingest throughput measurement.

Same default batch size for every platform (config.LOAD_BATCH_SIZE) — batch
size is a variable under test, not something hand-tuned per platform. If a
platform genuinely can't keep up at the default, override with
LOAD_BATCH_SIZE=<n> and that override is recorded in the result JSON, never
silently applied.
"""
from __future__ import annotations

import csv
import time

from benchmark import config
from benchmark.adapters.base import GraphAdapter


def _read_nodes(path) -> list[dict]:
    with open(path, newline="") as f:
        return [
            {
                "paperId": int(row["paperId"]),
                "year": row["year"] or None,
                "outDegree": int(row["outDegree"]),
                "inDegree": int(row["inDegree"]),
            }
            for row in csv.DictReader(f)
        ]


def _read_edges(path) -> list[dict]:
    with open(path, newline="") as f:
        return [
            {"fromPaperId": int(row["fromPaperId"]), "toPaperId": int(row["toPaperId"])}
            for row in csv.DictReader(f)
        ]


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def load_dataset(adapter: GraphAdapter, batch_size: int = config.LOAD_BATCH_SIZE) -> dict:
    """Wipes the target database, loads the processed dataset, and returns
    ingest throughput metrics. Node load and relationship load are timed
    separately (loading edges requires nodes to already exist via MATCH, so
    the two phases are never concurrent — timing them separately is honest
    about where the wall-clock time actually goes)."""
    nodes = _read_nodes(config.DATASET_NODES_CSV)
    edges = _read_edges(config.DATASET_EDGES_CSV)

    t_schema_start = time.perf_counter()
    adapter.reset_schema()
    t_schema_done = time.perf_counter()

    for batch in _chunks(nodes, batch_size):
        adapter.load_nodes(batch)
    t_nodes_done = time.perf_counter()

    for batch in _chunks(edges, batch_size):
        adapter.load_edges(batch)
    t_edges_done = time.perf_counter()

    schema_sec = t_schema_done - t_schema_start
    node_load_sec = t_nodes_done - t_schema_done
    edge_load_sec = t_edges_done - t_nodes_done
    total_sec = t_edges_done - t_schema_start

    verified_nodes = adapter.count_nodes()
    verified_edges = adapter.count_edges()

    return {
        "platform": adapter.spec.id,
        "batch_size": batch_size,
        "expected_node_count": len(nodes),
        "expected_edge_count": len(edges),
        "verified_node_count": verified_nodes,
        "verified_edge_count": verified_edges,
        "load_complete": verified_nodes == len(nodes) and verified_edges == len(edges),
        "schema_setup_sec": round(schema_sec, 3),
        "node_load_sec": round(node_load_sec, 3),
        "edge_load_sec": round(edge_load_sec, 3),
        "total_load_sec": round(total_sec, 3),
        "nodes_per_sec": round(len(nodes) / node_load_sec, 1) if node_load_sec > 0 else None,
        "rels_per_sec": round(len(edges) / edge_load_sec, 1) if edge_load_sec > 0 else None,
    }
