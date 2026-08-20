"""Abstract adapter interface. Every platform-specific adapter implements
these logical operations so benchmark/loader.py and benchmark/workloads.py
never see Cypher vs. AQL, or Bolt vs. RESP, differences directly.

Node model (identical across all five platforms):
    Label/collection: Paper
    Properties: paperId (int, unique key), year (str, optional), outDegree (int), inDegree (int)
Edge model:
    Type/collection: CITES, directed fromPaperId -> toPaperId, no properties.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from benchmark.config import PlatformSpec


class GraphAdapter(ABC):
    def __init__(self, spec: PlatformSpec):
        self.spec = spec

    # -- lifecycle ----------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- schema / loading -----------------------------------------------------
    @abstractmethod
    def reset_schema(self) -> None:
        """Delete all data and (re)create the Paper.paperId uniqueness
        constraint/index and the Paper.year index. Must be safe to call on
        an empty database."""

    @abstractmethod
    def load_nodes(self, rows: list[dict]) -> None:
        """rows: [{"paperId": int, "year": str|"", "outDegree": int, "inDegree": int}, ...]"""

    @abstractmethod
    def load_edges(self, rows: list[dict]) -> None:
        """rows: [{"fromPaperId": int, "toPaperId": int}, ...]"""

    @abstractmethod
    def count_nodes(self) -> int: ...

    @abstractmethod
    def count_edges(self) -> int: ...

    # -- read workloads -------------------------------------------------------
    @abstractmethod
    def point_lookup(self, paper_id: int) -> dict | None:
        """Fetch a single Paper by its unique paperId (primary-key lookup)."""

    @abstractmethod
    def indexed_lookup(self, year: str) -> list:
        """Fetch all Paper nodes with the given (indexed) year property."""

    @abstractmethod
    def traversal(self, paper_id: int, hops: int) -> list:
        """Distinct nodes reachable from paper_id via exactly `hops` outgoing
        CITES edges (1, 2, or 3)."""

    @abstractmethod
    def aggregation(self) -> list:
        """Group-by aggregation: count of Paper nodes per year."""

    # -- mixed read/write workload --------------------------------------------
    @abstractmethod
    def write_touch(self, paper_id: int) -> None:
        """Property UPDATE on an existing node — never create/delete, so
        node/edge counts and structure never drift across a benchmark run."""

    @abstractmethod
    def reset_benchmark_counter(self) -> None:
        """Clear the property write_touch() sets, restoring pristine state
        before a mixed-workload run. See RESET_BETWEEN_MIXED_RUNS."""

    # -- footprint --------------------------------------------------------------
    def footprint(self) -> dict:
        """Whatever the platform exposes about storage/memory use. Default:
        not observable — subclasses override where the platform exposes it."""
        return {"note": "not observable via driver"}
