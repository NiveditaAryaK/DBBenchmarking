"""CLI entrypoint tying loader + workloads together into one results JSON
per platform per run.

Usage:
    python -m benchmark.runner load  --platform cognodb
    python -m benchmark.runner bench --platform cognodb   # verifies data already loaded
    python -m benchmark.runner all   --platform cognodb   # load, then bench
    python -m benchmark.runner all   --platform all       # every platform with credentials in .env

Every platform run is wrapped in a try/except: a failure on one platform
(timeout, free-tier throttling, connection refused, incomplete load) is
recorded in that platform's result JSON with status="failed" and the real
exception message, and does not stop the other platforms in an
--platform all run. Per the assignment: honest caveats and failed runs are
reported, not hidden — and a platform is never benchmarked against a
dataset that didn't load correctly, since that would silently compare
platforms on different data.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from benchmark import config, workloads
from benchmark.adapters import make_adapter
from benchmark.loader import expected_counts, load_dataset


def _spec_dict(spec: config.PlatformSpec) -> dict:
    return {
        "id": spec.id,
        "name": spec.name,
        "deployment": spec.deployment,
        "driver": spec.driver,
        "query_language": spec.query_language,
        "advertised_vcpu": spec.advertised_vcpu,
        "advertised_ram": spec.advertised_ram,
        "advertised_disk": spec.advertised_disk,
        "region_note": spec.region_note,
    }


def run_load(platform_id: str) -> dict:
    spec = config.get_platform(platform_id)
    adapter = make_adapter(spec)
    adapter.connect()
    try:
        return load_dataset(adapter)
    finally:
        adapter.close()


def _verify_dataset_present(platform_id: str) -> None:
    """Confirms the platform currently holds exactly the expected dataset
    before a bench-only run — cheap (two count queries), and prevents
    silently benchmarking against stale or partial data from an earlier
    session."""
    spec = config.get_platform(platform_id)
    adapter = make_adapter(spec)
    adapter.connect()
    try:
        actual_nodes = adapter.count_nodes()
        actual_edges = adapter.count_edges()
    finally:
        adapter.close()
    expected_nodes, expected_edges = expected_counts()
    if actual_nodes != expected_nodes or actual_edges != expected_edges:
        raise RuntimeError(
            "Dataset verification failed before benchmark: expected "
            f"{expected_nodes} nodes / {expected_edges} relationships, "
            f"found {actual_nodes} nodes / {actual_edges} relationships. "
            f"Run `python -m benchmark.runner load --platform {platform_id}` first."
        )


def run_bench(platform_id: str, skip_mixed: bool = False) -> dict:
    """Read workloads + footprint + (optionally) the mixed-workload
    concurrency sweep. Caller is responsible for dataset verification."""
    spec = config.get_platform(platform_id)
    adapter = make_adapter(spec)
    adapter.connect()
    try:
        read_workloads = workloads.run_read_workloads(adapter)
        footprint = adapter.footprint()
    finally:
        adapter.close()

    mixed = workloads.run_mixed_workload_sweep(spec) if not skip_mixed else None
    return {"read_workloads": read_workloads, "footprint": footprint, "mixed_workload_sweep": mixed}


def run_command(platform_id: str, command: str, skip_mixed: bool = False) -> dict:
    """command: 'load' | 'bench' | 'all'. Always returns a fully-formed
    result dict (status='ok'|'failed') — never raises, so a failure on one
    platform never aborts a --platform all sweep."""
    spec = config.get_platform(platform_id)
    result: dict = {
        **_spec_dict(spec),
        "command": command,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "batch_size": config.LOAD_BATCH_SIZE,
        # Where this run's client actually executed from — e.g. "us-east4-vm"
        # vs "github-codespaces". Explicit BENCHMARK_CLIENT_ENV in .env wins;
        # otherwise auto-detected from GitHub Codespaces' own CODESPACES env
        # var (set automatically in every codespace), so this doesn't depend
        # on remembering to set it by hand. Surfaced in every generated
        # report so a mismatched-environment run is visible, not silently
        # blended in with real region-matched results.
        "client_env": os.getenv("BENCHMARK_CLIENT_ENV")
        or ("github-codespaces" if os.getenv("CODESPACES") == "true" else "unspecified"),
        "status": "ok",
        "error": None,
        "load": None,
        "read_workloads": None,
        "footprint": None,
        "mixed_workload_sweep": None,
    }
    try:
        if command in ("load", "all"):
            result["load"] = run_load(platform_id)
            if not result["load"]["load_complete"]:
                load = result["load"]
                raise RuntimeError(
                    "Dataset verification failed: expected "
                    f"{load['expected_node_count']} nodes / {load['expected_edge_count']} relationships, "
                    f"got {load['verified_node_count']} nodes / {load['verified_edge_count']} relationships."
                )
        if command in ("bench", "all"):
            if command == "bench":
                _verify_dataset_present(platform_id)
            bench = run_bench(platform_id, skip_mixed=skip_mixed)
            result.update(bench)
    except Exception as e:
        result["status"] = "failed"
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def save_result(result: dict):
    ts = result["timestamp_utc"].replace(":", "").replace("-", "").split(".")[0]
    path = config.RESULTS_RAW / f"{result['id']}_{result['command']}_{ts}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    return path


def _resolve_platform_ids(arg: str) -> list[str]:
    if arg != "all":
        return [arg]
    ids = [p.id for p in config.available_platforms()]
    if not ids:
        print("No platforms have all required env vars set — see .env.example.")
        sys.exit(1)
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="CognoDB benchmark harness CLI")
    parser.add_argument(
        "command",
        choices=["load", "bench", "all"],
        help="load: just load data. bench: verify+read+mixed workloads. all: load then bench.",
    )
    parser.add_argument(
        "--platform",
        required=True,
        help="platform id (cognodb, aura, falkordb, memgraph, arangodb) or 'all' for every platform with credentials in .env",
    )
    parser.add_argument(
        "--skip-mixed",
        action="store_true",
        help="skip the concurrency sweep (faster while developing/debugging)",
    )
    args = parser.parse_args()

    exit_code = 0
    for pid in _resolve_platform_ids(args.platform):
        print(f"=== {pid} ({args.command}) ===")
        result = run_command(pid, args.command, skip_mixed=args.skip_mixed)
        path = save_result(result)
        print(f"status={result['status']}" + (f" error={result['error']}" if result["error"] else ""))
        print(f"-> {path}")
        if result["status"] != "ok":
            exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
