"""Latency measurement: warm-up handling + percentile reporting.

Correctness invariant that every caller must respect: `fn` must fully
consume the database result before returning (materialize rows into a list,
call `.data()`/`.result_set`, etc. — never return a lazy cursor/result
object). If `fn` returns before the client has actually read the response,
the timer stops before the query is done, and this measures "time to start
receiving a response" instead of the client-observed latency the assignment
asks for. Every adapter method in benchmark/adapters/ already fully
materializes its results for exactly this reason.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np


def percentiles(latencies_ms: list[float]) -> dict:
    if not latencies_ms:
        return {"n": 0}
    arr = np.array(latencies_ms)
    return {
        "n": len(arr),
        "mean_ms": round(float(np.mean(arr)), 3),
        "p50_ms": round(float(np.percentile(arr, 50)), 3),
        "p95_ms": round(float(np.percentile(arr, 95)), 3),
        "p99_ms": round(float(np.percentile(arr, 99)), 3),
        "min_ms": round(float(np.min(arr)), 3),
        "max_ms": round(float(np.max(arr)), 3),
    }


def timed_run(fn: Callable[[], object], iterations: int, warmup: int) -> list[float]:
    """Runs fn() `warmup` times (discarded) then `iterations` times, timing
    only the measured phase. Use when every call is identical (e.g. the
    aggregation workload, which has no varying input). Returns per-call
    latencies in milliseconds."""
    for _ in range(warmup):
        fn()
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - t0) * 1000)
    return latencies


def timed_run_over(
    warmup_items: list, measured_items: list, fn: Callable[[object], object]
) -> list[float]:
    """Like timed_run, but each call uses a different, pre-sampled input
    (e.g. a different start node per traversal) so results aren't skewed by
    hammering a single cached value. warmup_items and measured_items are
    disjoint, dedicated sets (see data/prepare_dataset.py's query sample) —
    every warmup call is discarded and every measured call is timed; there's
    no overlap or truncation between the two phases."""
    for item in warmup_items:
        fn(item)
    latencies = []
    for item in measured_items:
        t0 = time.perf_counter()
        fn(item)
        latencies.append((time.perf_counter() - t0) * 1000)
    return latencies
