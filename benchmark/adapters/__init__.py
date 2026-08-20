from __future__ import annotations

from benchmark.config import PlatformSpec


def make_adapter(spec: PlatformSpec):
    """Factory: one adapter instance per connection. Workload concurrency is
    achieved by constructing one adapter per worker thread (see
    benchmark/workloads.py), not by sharing a single adapter across threads —
    that sidesteps driver thread-safety questions entirely and matches how a
    real concurrent client population would behave (each client = its own
    connection)."""
    if spec.driver == "bolt":
        from benchmark.adapters.bolt_adapter import BoltAdapter

        return BoltAdapter(spec)
    if spec.driver == "falkordb":
        from benchmark.adapters.falkordb_adapter import FalkorDBAdapter

        return FalkorDBAdapter(spec)
    if spec.driver == "arangodb":
        from benchmark.adapters.arangodb_adapter import ArangoDBAdapter

        return ArangoDBAdapter(spec)
    raise ValueError(f"No adapter for driver '{spec.driver}' (platform {spec.id})")
