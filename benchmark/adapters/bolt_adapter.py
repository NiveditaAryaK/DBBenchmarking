"""Bolt/Cypher adapter — shared by CognoDB Cloud, Neo4j AuraDB Free, and
self-hosted Memgraph. All three speak the Bolt protocol via the official
`neo4j` Python driver, which is the entire point of CognoDB and Memgraph
being "drop in a Bolt URI, no other code changes" per the assignment.

The one real dialect split is schema DDL: Memgraph uses the older
`CREATE CONSTRAINT ON (n:Label) ASSERT ...` / `CREATE INDEX ON :Label(prop)`
syntax, while Neo4j-family servers (CognoDB, Aura) use Neo4j 5.x's
`CREATE CONSTRAINT ... FOR (n:Label) REQUIRE ...` syntax. Everything else —
loading, traversal, lookups, aggregation, mixed workload — is one identical
Cypher query text across all three.
"""
from __future__ import annotations

import os

from neo4j import GraphDatabase

from benchmark.adapters.base import GraphAdapter
from benchmark.config import MIXED_WRITE_PROPERTY


class BoltAdapter(GraphAdapter):
    def _conn_value(self, suffix: str) -> str | None:
        for key in (*self.spec.required_env, *self.spec.optional_env):
            if key.endswith(suffix):
                val = os.getenv(key)
                if val:
                    return val
        return None

    def connect(self) -> None:
        uri = self._conn_value("_URI")
        user = self._conn_value("_USER")
        password = self._conn_value("_PASSWORD")
        auth = (user, password) if user else None
        self.driver = GraphDatabase.driver(uri, auth=auth)

    def close(self) -> None:
        self.driver.close()

    def reset_schema(self) -> None:
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n").consume()
            if self.spec.id == "memgraph":
                for stmt in (
                    "DROP CONSTRAINT ON (n:Paper) ASSERT n.paperId IS UNIQUE",
                    "DROP INDEX ON :Paper(paperId)",
                    "DROP INDEX ON :Paper(year)",
                ):
                    try:
                        s.run(stmt).consume()
                    except Exception:
                        pass
                s.run("CREATE CONSTRAINT ON (n:Paper) ASSERT n.paperId IS UNIQUE").consume()
                s.run("CREATE INDEX ON :Paper(year)").consume()
            else:
                s.run(
                    "CREATE CONSTRAINT paper_id_unique IF NOT EXISTS "
                    "FOR (n:Paper) REQUIRE n.paperId IS UNIQUE"
                ).consume()
                s.run(
                    "CREATE INDEX paper_year_idx IF NOT EXISTS FOR (n:Paper) ON (n.year)"
                ).consume()

    def load_nodes(self, rows: list[dict]) -> None:
        query = """
        UNWIND $rows AS row
        MERGE (p:Paper {paperId: row.paperId})
        SET p.outDegree = row.outDegree, p.inDegree = row.inDegree
        FOREACH (_ IN CASE WHEN row.year <> '' THEN [1] ELSE [] END | SET p.year = row.year)
        """
        with self.driver.session() as s:
            s.run(query, rows=rows).consume()

    def load_edges(self, rows: list[dict]) -> None:
        query = """
        UNWIND $rows AS row
        MATCH (a:Paper {paperId: row.fromPaperId})
        MATCH (b:Paper {paperId: row.toPaperId})
        MERGE (a)-[:CITES]->(b)
        """
        with self.driver.session() as s:
            s.run(query, rows=rows).consume()

    def count_nodes(self) -> int:
        with self.driver.session() as s:
            return s.run("MATCH (n:Paper) RETURN count(n) AS c").single()["c"]

    def count_edges(self) -> int:
        with self.driver.session() as s:
            return s.run("MATCH ()-[r:CITES]->() RETURN count(r) AS c").single()["c"]

    def point_lookup(self, paper_id: int) -> dict | None:
        query = (
            "MATCH (p:Paper {paperId: $id}) "
            "RETURN p.paperId AS paperId, p.year AS year, p.outDegree AS outDegree, p.inDegree AS inDegree"
        )
        with self.driver.session() as s:
            rec = s.run(query, id=paper_id).single()
            return dict(rec) if rec else None

    def indexed_lookup(self, year: str) -> list:
        query = "MATCH (p:Paper {year: $year}) RETURN p.paperId AS paperId"
        with self.driver.session() as s:
            return [dict(r) for r in s.run(query, year=year)]

    def traversal(self, paper_id: int, hops: int) -> list:
        hops = int(hops)
        query = (
            f"MATCH (p:Paper {{paperId: $id}})-[:CITES*{hops}..{hops}]->(x) "
            "RETURN DISTINCT x.paperId AS paperId"
        )
        with self.driver.session() as s:
            return [r["paperId"] for r in s.run(query, id=paper_id)]

    def aggregation(self) -> list:
        query = (
            "MATCH (p:Paper) WHERE p.year IS NOT NULL "
            "RETURN p.year AS year, count(p) AS count ORDER BY year"
        )
        with self.driver.session() as s:
            return [dict(r) for r in s.run(query)]

    def write_touch(self, paper_id: int) -> None:
        query = f"MATCH (p:Paper {{paperId: $id}}) SET p.{MIXED_WRITE_PROPERTY} = coalesce(p.{MIXED_WRITE_PROPERTY}, 0) + 1"
        with self.driver.session() as s:
            s.run(query, id=paper_id).consume()

    def reset_benchmark_counter(self) -> None:
        query = f"MATCH (p:Paper) REMOVE p.{MIXED_WRITE_PROPERTY}"
        with self.driver.session() as s:
            s.run(query).consume()

    def footprint(self) -> dict:
        out = {"nodes": self.count_nodes(), "edges": self.count_edges()}
        if self.spec.id == "memgraph":
            try:
                with self.driver.session() as s:
                    rows = [dict(r) for r in s.run("SHOW STORAGE INFO")]
                out["storage_info"] = rows
            except Exception as e:
                out["storage_info_error"] = str(e)
        else:
            out["note"] = (
                "Byte-level storage size is not exposed over Bolt on this "
                "platform's free tier; see the platform console/dashboard."
            )
        return out
