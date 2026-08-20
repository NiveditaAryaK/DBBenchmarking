"""ArangoDB adapter — the AQL, multi-model comparator. Deliberately the most
different platform in the set: no Cypher at all, a document model (`_key` /
`_from` / `_to`) instead of a native property-graph model, and traversal
expressed as AQL's `FOR v IN 1..N OUTBOUND` syntax instead of a Cypher
variable-length relationship pattern.
"""
from __future__ import annotations

import os

from arango import ArangoClient

from benchmark.adapters.base import GraphAdapter
from benchmark.config import MIXED_WRITE_PROPERTY

NODE_COLLECTION = "Paper"
EDGE_COLLECTION = "CITES"


class ArangoDBAdapter(GraphAdapter):
    def connect(self) -> None:
        url = os.getenv("ARANGO_URL")
        user = os.getenv("ARANGO_USER")
        password = os.getenv("ARANGO_PASSWORD")
        db_name = os.getenv("ARANGO_DB")

        self.client = ArangoClient(hosts=url)
        sys_db = self.client.db("_system", username=user, password=password)
        if not sys_db.has_database(db_name):
            sys_db.create_database(db_name)
        self.db = self.client.db(db_name, username=user, password=password)

    def close(self) -> None:
        pass  # python-arango has no persistent connection object to close

    def reset_schema(self) -> None:
        if self.db.has_collection(NODE_COLLECTION):
            self.db.delete_collection(NODE_COLLECTION)
        if self.db.has_collection(EDGE_COLLECTION):
            self.db.delete_collection(EDGE_COLLECTION)
        papers = self.db.create_collection(NODE_COLLECTION)
        papers.add_persistent_index(fields=["year"])
        self.db.create_collection(EDGE_COLLECTION, edge=True)

    def load_nodes(self, rows: list[dict]) -> None:
        docs = [
            {
                "_key": str(r["paperId"]),
                "paperId": r["paperId"],
                "year": r["year"] if r.get("year") else None,
                "outDegree": r["outDegree"],
                "inDegree": r["inDegree"],
            }
            for r in rows
        ]
        self.db.collection(NODE_COLLECTION).insert_many(
            docs, overwrite=True, keep_none=False, silent=True
        )

    def load_edges(self, rows: list[dict]) -> None:
        docs = [
            {
                "_from": f"{NODE_COLLECTION}/{r['fromPaperId']}",
                "_to": f"{NODE_COLLECTION}/{r['toPaperId']}",
            }
            for r in rows
        ]
        self.db.collection(EDGE_COLLECTION).insert_many(docs, overwrite=True, silent=True)

    def count_nodes(self) -> int:
        return self.db.collection(NODE_COLLECTION).count()

    def count_edges(self) -> int:
        return self.db.collection(EDGE_COLLECTION).count()

    def point_lookup(self, paper_id: int) -> dict | None:
        doc = self.db.collection(NODE_COLLECTION).get(str(paper_id))
        if doc is None:
            return None
        return {
            "paperId": doc["paperId"],
            "year": doc.get("year"),
            "outDegree": doc["outDegree"],
            "inDegree": doc["inDegree"],
        }

    def indexed_lookup(self, year: str) -> list:
        cursor = self.db.aql.execute(
            "FOR p IN Paper FILTER p.year == @year RETURN p.paperId",
            bind_vars={"year": year},
        )
        return [{"paperId": pid} for pid in cursor]

    def traversal(self, paper_id: int, hops: int) -> list:
        hops = int(hops)
        query = (
            f"FOR v IN {hops}..{hops} OUTBOUND @start {EDGE_COLLECTION} "
            "RETURN DISTINCT v.paperId"
        )
        cursor = self.db.aql.execute(query, bind_vars={"start": f"{NODE_COLLECTION}/{paper_id}"})
        return list(cursor)

    def aggregation(self) -> list:
        query = (
            "FOR p IN Paper FILTER p.year != null "
            "COLLECT year = p.year WITH COUNT INTO count "
            "SORT year RETURN {year, count}"
        )
        return list(self.db.aql.execute(query))

    def write_touch(self, paper_id: int) -> None:
        query = (
            f"FOR p IN {NODE_COLLECTION} FILTER p._key == @key "
            f"UPDATE p WITH {{ {MIXED_WRITE_PROPERTY}: "
            f"(p.{MIXED_WRITE_PROPERTY} == null ? 0 : p.{MIXED_WRITE_PROPERTY}) + 1 }} "
            f"IN {NODE_COLLECTION}"
        )
        self.db.aql.execute(query, bind_vars={"key": str(paper_id)})

    def reset_benchmark_counter(self) -> None:
        query = (
            f"FOR p IN {NODE_COLLECTION} "
            f"UPDATE p WITH {{ {MIXED_WRITE_PROPERTY}: null }} IN {NODE_COLLECTION} "
            "OPTIONS { keepNull: false }"
        )
        self.db.aql.execute(query)

    def footprint(self) -> dict:
        out = {
            "nodes": self.count_nodes(),
            "edges": self.count_edges(),
            "note": (
                "Container capped at 0.5 vCPU / 512 MB RAM (docker-compose); "
                "byte-level graph storage not separately summarized here — see collection statistics below."
            ),
        }
        try:
            out["node_collection_statistics"] = self.db.collection(NODE_COLLECTION).statistics()
            out["edge_collection_statistics"] = self.db.collection(EDGE_COLLECTION).statistics()
        except Exception as e:
            out["statistics_error"] = str(e)
        return out
