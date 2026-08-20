"""FalkorDB Cloud adapter. FalkorDB speaks a Cypher subset (openCypher-ish)
over the Redis RESP protocol via GRAPH.QUERY, not Bolt — a genuinely
different wire protocol and query engine (sparse-matrix/GraphBLAS-based)
from the Bolt trio, which is exactly why it's in the comparison set.

Two real dialect differences from Neo4j/Memgraph Cypher, both handled here:
  - No FOREACH, so the "only set `year` if present" trick used in
    bolt_adapter.py doesn't work — loading is split into two passes instead.
  - Indexes/constraints are created via the client's typed helper methods
    (GRAPH.CONSTRAINT CREATE), not DDL Cypher statements.
"""
from __future__ import annotations

import os

from falkordb import FalkorDB

from benchmark.adapters.base import GraphAdapter
from benchmark.config import MIXED_WRITE_PROPERTY


class FalkorDBAdapter(GraphAdapter):
    def connect(self) -> None:
        host = os.getenv("FALKORDB_HOST", "")
        # FalkorDB Cloud dashboards often show the endpoint as "host:port" in
        # one field — strip an embedded port so FALKORDB_HOST can be pasted
        # either as host-only or host:port without breaking the connection.
        if ":" in host:
            host, _, embedded_port = host.rpartition(":")
            port = int(os.getenv("FALKORDB_PORT") or embedded_port)
        else:
            port = int(os.getenv("FALKORDB_PORT", "6379"))
        password = os.getenv("FALKORDB_PASSWORD")
        # FalkorDB Cloud instances use Redis ACL auth, which requires a
        # username (not just a password) — self-hosted/local FalkorDB
        # typically doesn't set FALKORDB_USER at all, so this defaults to None.
        username = os.getenv("FALKORDB_USER") or None
        graph_name = os.getenv("FALKORDB_GRAPH", "cit_hepph")
        # Confirmed against a real FalkorDB Cloud free instance: ssl=True
        # hangs until socket_timeout on this endpoint (TLS handshake never
        # completes), while ssl=False connects and queries in ~2s. Overridable
        # via FALKORDB_SSL for instances that do require it.
        use_ssl = os.getenv("FALKORDB_SSL", "false").lower() == "true"
        self.client = FalkorDB(
            host=host,
            port=port,
            username=username,
            password=password,
            ssl=use_ssl,
            socket_connect_timeout=15,
            socket_timeout=15,
        )
        self.graph = self.client.select_graph(graph_name)

    def close(self) -> None:
        pass  # falkordb client has no explicit close; connection is pooled by redis-py

    def reset_schema(self) -> None:
        try:
            self.graph.delete()
        except Exception:
            pass  # graph didn't exist yet
        self.graph = self.client.select_graph(self.graph.name)
        self.graph.create_node_unique_constraint("Paper", "paperId")
        self.graph.create_node_range_index("Paper", "year")

    def load_nodes(self, rows: list[dict]) -> None:
        base_rows = [
            {"paperId": r["paperId"], "outDegree": r["outDegree"], "inDegree": r["inDegree"]}
            for r in rows
        ]
        self.graph.query(
            "UNWIND $rows AS row "
            "MERGE (p:Paper {paperId: row.paperId}) "
            "SET p.outDegree = row.outDegree, p.inDegree = row.inDegree",
            {"rows": base_rows},
        )
        year_rows = [{"paperId": r["paperId"], "year": r["year"]} for r in rows if r.get("year")]
        if year_rows:
            self.graph.query(
                "UNWIND $rows AS row "
                "MATCH (p:Paper {paperId: row.paperId}) "
                "SET p.year = row.year",
                {"rows": year_rows},
            )

    def load_edges(self, rows: list[dict]) -> None:
        self.graph.query(
            "UNWIND $rows AS row "
            "MATCH (a:Paper {paperId: row.fromPaperId}) "
            "MATCH (b:Paper {paperId: row.toPaperId}) "
            "MERGE (a)-[:CITES]->(b)",
            {"rows": rows},
        )

    def count_nodes(self) -> int:
        res = self.graph.query("MATCH (n:Paper) RETURN count(n)")
        return res.result_set[0][0]

    def count_edges(self) -> int:
        res = self.graph.query("MATCH ()-[r:CITES]->() RETURN count(r)")
        return res.result_set[0][0]

    def point_lookup(self, paper_id: int) -> dict | None:
        res = self.graph.query(
            "MATCH (p:Paper {paperId: $id}) "
            "RETURN p.paperId, p.year, p.outDegree, p.inDegree",
            {"id": paper_id},
        )
        if not res.result_set:
            return None
        row = res.result_set[0]
        return {"paperId": row[0], "year": row[1], "outDegree": row[2], "inDegree": row[3]}

    def indexed_lookup(self, year: str) -> list:
        res = self.graph.query("MATCH (p:Paper {year: $year}) RETURN p.paperId", {"year": year})
        return [{"paperId": row[0]} for row in res.result_set]

    def traversal(self, paper_id: int, hops: int) -> list:
        hops = int(hops)
        query = (
            f"MATCH (p:Paper {{paperId: $id}})-[:CITES*{hops}..{hops}]->(x) "
            "RETURN DISTINCT x.paperId"
        )
        res = self.graph.query(query, {"id": paper_id})
        return [row[0] for row in res.result_set]

    def aggregation(self) -> list:
        res = self.graph.query(
            "MATCH (p:Paper) WHERE p.year IS NOT NULL "
            "RETURN p.year AS year, count(p) AS count ORDER BY year"
        )
        return [{"year": row[0], "count": row[1]} for row in res.result_set]

    def write_touch(self, paper_id: int) -> None:
        query = (
            f"MATCH (p:Paper {{paperId: $id}}) "
            f"SET p.{MIXED_WRITE_PROPERTY} = coalesce(p.{MIXED_WRITE_PROPERTY}, 0) + 1"
        )
        self.graph.query(query, {"id": paper_id})

    def reset_benchmark_counter(self) -> None:
        self.graph.query(f"MATCH (p:Paper) REMOVE p.{MIXED_WRITE_PROPERTY}")

    def footprint(self) -> dict:
        return {
            "nodes": self.count_nodes(),
            "edges": self.count_edges(),
            "note": "FalkorDB Cloud free tier does not expose per-graph memory usage over the client API.",
        }
