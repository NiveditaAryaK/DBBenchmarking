"""Read workloads (point lookup, indexed lookup, 1/2/3-hop traversal,
aggregation) and the concurrent mixed read/write workload.

Every read workload draws its inputs from data/processed/query_start_nodes.json
— the same warmup/measured node (or year) sequence for every platform, see
data/prepare_dataset.py and benchmark/stats.py for why.
"""
from __future__ import annotations

import json
import random
import threading
import time

from benchmark import config
from benchmark.adapters import make_adapter
from benchmark.adapters.base import GraphAdapter
from benchmark.config import PlatformSpec
from benchmark.stats import percentiles, timed_run, timed_run_over

if abs(sum(config.MIXED_READ_MIX.values()) - 1.0) > 1e-9:
    raise ValueError(
        f"MIXED_READ_MIX must sum to 1.0, got {sum(config.MIXED_READ_MIX.values())}"
    )

# How long we'll wait for every concurrent worker to finish connecting
# before giving up on synchronized start (see run_mixed_workload). Not a
# per-connection timeout — the driver's own connection timeout governs that.
CONNECT_BARRIER_TIMEOUT_SEC = 60


def load_query_sample() -> dict:
    with open(config.QUERY_SAMPLE_FILE) as f:
        return json.load(f)


# -- read workloads -----------------------------------------------------------
def run_point_lookup(adapter: GraphAdapter, sample: dict) -> dict:
    pl = sample["point_lookup"]
    latencies = timed_run_over(pl["warmup"], pl["measured"], adapter.point_lookup)
    return percentiles(latencies)


def run_indexed_lookup(adapter: GraphAdapter, sample: dict) -> dict:
    il = sample["indexed_lookup_years"]
    latencies = timed_run_over(il["warmup"], il["measured"], adapter.indexed_lookup)
    return percentiles(latencies)


def run_traversal(adapter: GraphAdapter, sample: dict, hops: int) -> dict:
    tr = sample["traversal"]
    latencies = timed_run_over(tr["warmup"], tr["measured"], lambda pid: adapter.traversal(pid, hops))
    return percentiles(latencies)


def run_aggregation(adapter: GraphAdapter) -> dict:
    latencies = timed_run(adapter.aggregation, config.MEASURED_ITERATIONS, config.WARMUP_ITERATIONS)
    return percentiles(latencies)


def run_read_workloads(adapter: GraphAdapter) -> dict:
    sample = load_query_sample()
    return {
        "point_lookup": run_point_lookup(adapter, sample),
        "indexed_lookup": run_indexed_lookup(adapter, sample),
        "traversal": {f"hop{h}": run_traversal(adapter, sample, h) for h in config.HOP_DEPTHS},
        "aggregation": run_aggregation(adapter),
    }


# -- mixed concurrent read/write workload --------------------------------------
def _op_boundaries() -> tuple[float, float, float]:
    """Cumulative probability cutoffs for [point_lookup, hop1, hop2] within
    [0, MIXED_WORKLOAD_READ_RATIO); anything beyond that up to 1.0 is a write."""
    r = config.MIXED_WORKLOAD_READ_RATIO
    mix = config.MIXED_READ_MIX
    b1 = mix["point_lookup"] * r
    b2 = b1 + mix["hop1"] * r
    b3 = b2 + mix["hop2"] * r
    return b1, b2, b3


def _mixed_worker(
    spec: PlatformSpec,
    stop_event: threading.Event,
    rng_seed: int,
    sample: dict,
    results: list,
    idx: int,
    ready_barrier: threading.Barrier,
    connected_flags: list,
) -> None:
    lookup_pool = sample["point_lookup"]["warmup"] + sample["point_lookup"]["measured"]
    traversal_pool = sample["traversal"]["warmup"] + sample["traversal"]["measured"]
    b1, b2, b3 = _op_boundaries()
    op_counts = {"point_lookup": 0, "hop1": 0, "hop2": 0, "write": 0}
    errors = 0
    total_ops = 0
    runtime_errors: list = []
    connect_error = None
    adapter = None

    try:
        try:
            adapter = make_adapter(spec)
            adapter.connect()
            connected_flags[idx] = True
        except Exception as e:
            connect_error = str(e)

        # Every worker reaches the barrier regardless of connect outcome, so
        # the main thread can decide "did everyone connect?" in one place
        # rather than each worker guessing independently.
        try:
            ready_barrier.wait(timeout=CONNECT_BARRIER_TIMEOUT_SEC)
        except threading.BrokenBarrierError:
            connect_error = connect_error or "timed out waiting for other workers to connect"

        # stop_event may already be set here if the main thread aborted the
        # run because some other worker failed to connect — in that case
        # this loop must not start at all, not even for one iteration.
        if connect_error is None:
            rng = random.Random(rng_seed)
            while not stop_event.is_set():
                r = rng.random()
                try:
                    if r < b1:
                        adapter.point_lookup(rng.choice(lookup_pool))
                        op_counts["point_lookup"] += 1
                    elif r < b2:
                        adapter.traversal(rng.choice(traversal_pool), 1)
                        op_counts["hop1"] += 1
                    elif r < b3:
                        adapter.traversal(rng.choice(traversal_pool), 2)
                        op_counts["hop2"] += 1
                    else:
                        adapter.write_touch(rng.choice(lookup_pool))
                        op_counts["write"] += 1
                    total_ops += 1
                except Exception as e:
                    errors += 1
                    if len(runtime_errors) < 5:
                        runtime_errors.append(str(e))
    finally:
        # Cleanup belongs here, not inside the "did we run the workload"
        # branch above — a connection that was successfully opened must be
        # closed even if the barrier broke or the run was aborted.
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass

    results[idx] = {
        "op_counts": op_counts,
        "total_ops": total_ops,
        "errors": errors,
        "runtime_errors": runtime_errors,
        "connect_error": connect_error,
    }


def run_mixed_workload(
    spec: PlatformSpec,
    concurrency: int,
    duration_sec: float = config.MIXED_WORKLOAD_DURATION_SEC,
) -> dict:
    """One concurrency-level run: `concurrency` threads, each with its own
    connection (see benchmark/adapters/__init__.py factory docstring for why
    a shared adapter isn't used), hammering the fixed 80/20 read/write mix
    for `duration_sec` wall-clock seconds of *sustained* throughput.

    All-or-nothing connect: if any worker fails to connect (or the barrier
    times out), the run is aborted before the clock ever starts and reported
    with run_valid=False. A "40-client" result where only 35 clients actually
    connected isn't the same experiment as a real 40-client run, so it's
    never silently reported as one — see run_valid in the returned dict.
    """
    if config.RESET_BETWEEN_MIXED_RUNS:
        reset_adapter = make_adapter(spec)
        reset_adapter.connect()
        try:
            reset_adapter.reset_benchmark_counter()
        finally:
            reset_adapter.close()

    sample = load_query_sample()
    stop_event = threading.Event()
    ready_barrier = threading.Barrier(concurrency + 1)  # +1 for this (main) thread
    results: list = [None] * concurrency
    connected_flags = [False] * concurrency
    threads = [
        threading.Thread(
            target=_mixed_worker,
            args=(
                spec,
                stop_event,
                config.RANDOM_QUERY_SEED + i,
                sample,
                results,
                i,
                ready_barrier,
                connected_flags,
            ),
        )
        for i in range(concurrency)
    ]

    for t in threads:
        t.start()

    barrier_broke = False
    try:
        ready_barrier.wait(timeout=CONNECT_BARRIER_TIMEOUT_SEC)
    except threading.BrokenBarrierError:
        barrier_broke = True

    all_connected = (not barrier_broke) and all(connected_flags)
    if not all_connected:
        # Abort before measuring anything — workers that did connect will
        # see this immediately and skip their workload loop entirely.
        stop_event.set()
        for t in threads:
            t.join()
        connect_failures = [r["connect_error"] for r in results if r and r.get("connect_error")]
        return {
            "concurrency": concurrency,
            "run_valid": False,
            "reason": "not all workers connected within timeout; run aborted before measurement started",
            "workers_connected": sum(connected_flags),
            "workers_failed_to_connect": concurrency - sum(connected_flags),
            "connect_errors": connect_failures[:5],
            "duration_sec": 0,
            "successful_ops": 0,
            "failed_ops": 0,
            "error_rate": None,
            "qps": None,
            "op_breakdown": {},
        }

    t_start = time.perf_counter()
    timer = threading.Timer(duration_sec, stop_event.set)
    timer.start()
    for t in threads:
        t.join()
    actual_duration = time.perf_counter() - t_start
    timer.cancel()

    total_ops = sum(r["total_ops"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    attempted_ops = total_ops + total_errors
    op_breakdown: dict = {}
    runtime_errors: list = []
    for r in results:
        for k, v in r["op_counts"].items():
            op_breakdown[k] = op_breakdown.get(k, 0) + v
        if len(runtime_errors) < 5:
            runtime_errors.extend(r["runtime_errors"][: 5 - len(runtime_errors)])

    return {
        "concurrency": concurrency,
        "run_valid": True,
        "workers_connected": concurrency,
        "workers_failed_to_connect": 0,
        "duration_sec": round(actual_duration, 3),
        "successful_ops": total_ops,
        "failed_ops": total_errors,
        "error_rate": round(total_errors / attempted_ops, 4) if attempted_ops else 0,
        "runtime_errors": runtime_errors,
        "qps": round(total_ops / actual_duration, 2) if actual_duration > 0 else None,
        "op_breakdown": op_breakdown,
    }


def run_mixed_workload_sweep(
    spec: PlatformSpec, concurrency_levels: tuple[int, ...] = config.CONCURRENCY_LEVELS
) -> dict:
    return {str(c): run_mixed_workload(spec, c) for c in concurrency_levels}
