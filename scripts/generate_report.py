"""Turns results/raw/*.json into results/RESULTS.md (markdown tables),
results/dashboard.html (a browsable summary), and PNG charts in
results/charts/. README.md links to these rather than hand-copying numbers,
so re-running a benchmark and re-running this script is the entire
"update the README" workflow.

Usage: python -m scripts.generate_report
"""
from __future__ import annotations

import hashlib
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from benchmark import config

# Fixed platform -> color mapping (dataviz skill's validated categorical
# palette, slots assigned in a fixed order — never re-cycled per chart, so
# "CognoDB" is the same color in every figure in this report).
PLATFORM_COLOR = {
    "cognodb": "#2a78d6",   # slot 1 blue
    "aura": "#eb6834",      # slot 2 orange
    "falkordb": "#1baf7a",  # slot 3 aqua
    "memgraph": "#eda100",  # slot 4 yellow
    "arangodb": "#e87ba4",  # slot 5 magenta
}
PLATFORM_ORDER = list(config.PLATFORMS.keys())  # cognodb, aura, falkordb, memgraph, arangodb
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID_COLOR = "#d8d7d0"

FAIRNESS_BANNER = (
    "Managed free tiers do not expose identical underlying hardware; self-hosted "
    "comparators (Memgraph, ArangoDB) were explicitly CPU/RAM-capped to match "
    "CognoDB's provisioned instance instead of left unconstrained. Results are "
    "workload- and resource-tier-specific, not a universal ranking of these "
    "databases — see the README methodology and caveats before drawing "
    "broader conclusions."
)

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": GRID_COLOR,
        "axes.labelcolor": TEXT_PRIMARY,
        "text.color": TEXT_PRIMARY,
        "xtick.color": TEXT_SECONDARY,
        "ytick.color": TEXT_SECONDARY,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 10,
    }
)


def load_latest_results() -> dict:
    """Latest status='ok' result per platform (falls back to the latest
    result regardless of status if nothing succeeded, so failures are still
    visible in the report instead of silently omitted)."""
    by_platform: dict[str, dict] = {}
    for path in sorted(config.RESULTS_RAW.glob("*.json")):
        with open(path) as f:
            result = json.load(f)
        pid = result.get("id")
        if not pid:
            continue
        existing = by_platform.get(pid)
        if existing is None:
            by_platform[pid] = result
            continue
        existing_ok = existing.get("status") == "ok"
        new_ok = result.get("status") == "ok"
        if (new_ok and not existing_ok) or (
            new_ok == existing_ok and result["timestamp_utc"] > existing["timestamp_utc"]
        ):
            by_platform[pid] = result
    return by_platform


def _fmt(val, digits=1):
    return "—" if val is None else f"{val:.{digits}f}"


def _platform_name(pid: str) -> str:
    return config.PLATFORMS[pid].name if pid in config.PLATFORMS else pid


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# -- winner-by-workload -------------------------------------------------------------
def _read_metric(r: dict, *path, default=None):
    node = r
    for p in path:
        if not isinstance(node, dict):
            return default
        node = node.get(p)
    return node if node is not None else default


def _mixed_qps(r: dict, concurrency: int):
    run = (r.get("mixed_workload_sweep") or {}).get(str(concurrency))
    if run and run.get("run_valid"):
        return run.get("qps")
    return None


def compute_winners(results: dict) -> list[dict]:
    winners: list[dict] = []

    def add(label: str, getter, unit: str, pick_min: bool):
        best = None
        for pid in PLATFORM_ORDER:
            v = getter(results.get(pid) or {})
            if v is None:
                continue
            if best is None or (v < best[1] if pick_min else v > best[1]):
                best = (pid, v)
        if best:
            winners.append({"workload": label, "platform": _platform_name(best[0]), "value": f"{best[1]:.2f} {unit}"})
        else:
            winners.append({"workload": label, "platform": "—", "value": "no data"})

    add("Point lookup (p95)", lambda r: _read_metric(r, "read_workloads", "point_lookup", "p95_ms"), "ms", True)
    add("Indexed lookup (p95)", lambda r: _read_metric(r, "read_workloads", "indexed_lookup", "p95_ms"), "ms", True)
    for h in config.HOP_DEPTHS:
        add(
            f"{h}-hop traversal (p95)",
            lambda r, h=h: _read_metric(r, "read_workloads", "traversal", f"hop{h}", "p95_ms"),
            "ms",
            True,
        )
    add("Aggregation (p95)", lambda r: _read_metric(r, "read_workloads", "aggregation", "p95_ms"), "ms", True)
    add("Ingest throughput (nodes/sec)", lambda r: _read_metric(r, "load", "nodes_per_sec"), "nodes/s", False)
    add("Ingest throughput (rels/sec)", lambda r: _read_metric(r, "load", "rels_per_sec"), "rels/s", False)
    for c in config.CONCURRENCY_LEVELS:
        add(f"Mixed workload QPS @ {c} clients", lambda r, c=c: _mixed_qps(r, c), "qps", False)

    return winners


# -- markdown -----------------------------------------------------------------------
def build_dataset_section() -> list[str]:
    lines = ["## Dataset\n"]
    if config.DATASET_MANIFEST.exists():
        with open(config.DATASET_MANIFEST) as f:
            manifest = json.load(f)
        sg = manifest["sample_graph"]
        lines.append(f"- Source: {manifest['source']}")
        lines.append(
            f"- Sampling method: {manifest['sampling_method']} "
            f"(seed={manifest['sampling_params']['seed']}, {manifest['sampling_params']['fire_restarts']} fire restarts)"
        )
        lines.append(
            f"- Sample graph: **{sg['nodes']:,} nodes / {sg['edges']:,} relationships**, "
            f"avg degree {sg['avg_degree']}, {sg['connected_components']} connected component(s)"
        )
        np_ = manifest["node_properties"]
        lines.append(
            f"- Year property: {np_['year_present_count']:,} papers with a real recorded year, "
            f"{np_['year_missing_count']:,} without (no synthetic years — see caveats)"
        )
    if config.DATASET_NODES_CSV.exists() and config.DATASET_EDGES_CSV.exists():
        lines.append(f"- `nodes.csv` sha256: `{_sha256_file(config.DATASET_NODES_CSV)}`")
        lines.append(f"- `edges.csv` sha256: `{_sha256_file(config.DATASET_EDGES_CSV)}`")
        lines.append(
            "  (fingerprints of the processed dataset actually used to generate this report — "
            "regenerate via `make dataset` and diff against these to confirm you reproduced the exact same graph)"
        )
    lines.append("")
    return lines


def build_winner_section(winners: list[dict]) -> list[str]:
    lines = ["## Winner by workload\n"]
    lines.append("| Workload | Winner | Value |")
    lines.append("|---|---|---|")
    for w in winners:
        lines.append(f"| {w['workload']} | {w['platform']} | {w['value']} |")
    lines.append("")
    lines.append(
        "_These are winners only for this specific dataset, query set, and resource-capped setup — "
        'not a general claim about which database is "best." A different dataset size, query mix, or '
        "hardware tier could change every row._\n"
    )
    return lines


def build_markdown(results: dict, winners: list[dict]) -> str:
    lines = [
        "# Results\n",
        "Generated by `python -m scripts.generate_report`. Do not hand-edit — edit the harness and re-run.\n",
        f"> **Fairness note:** {FAIRNESS_BANNER}\n",
    ]

    lines.append("## Platform specs\n")
    lines.append("| Platform | Deployment | Driver | Query language | vCPU | RAM | Disk |")
    lines.append("|---|---|---|---|---|---|---|")
    for pid in PLATFORM_ORDER:
        spec = config.PLATFORMS[pid]
        lines.append(
            f"| {spec.name} | {spec.deployment} | {spec.driver} | {spec.query_language} | "
            f"{spec.advertised_vcpu} | {spec.advertised_ram} | {spec.advertised_disk} |"
        )
    lines.append("")

    lines.extend(build_dataset_section())

    lines.append("## Run status\n")
    lines.append("| Platform | Status | Timestamp (UTC) | Error |")
    lines.append("|---|---|---|---|")
    for pid in PLATFORM_ORDER:
        r = results.get(pid)
        if r is None:
            lines.append(f"| {_platform_name(pid)} | not run | — | — |")
        else:
            err = (r.get("error") or "").replace("|", "/")[:120]
            lines.append(f"| {_platform_name(pid)} | {r['status']} | {r['timestamp_utc']} | {err} |")
    lines.append("")

    lines.append("## Data loading\n")
    lines.append("| Platform | Nodes/sec | Rels/sec | Total load time (s) | Load verified |")
    lines.append("|---|---|---|---|---|")
    for pid in PLATFORM_ORDER:
        load = _read_metric(results.get(pid) or {}, "load")
        if not load:
            lines.append(f"| {_platform_name(pid)} | — | — | — | — |")
            continue
        lines.append(
            f"| {_platform_name(pid)} | {_fmt(load['nodes_per_sec'])} | {_fmt(load['rels_per_sec'])} | "
            f"{_fmt(load['total_load_sec'], 1)} | {'yes' if load['load_complete'] else 'NO — see caveats'} |"
        )
    lines.append("")

    for hop in config.HOP_DEPTHS:
        lines.append(f"## {hop}-hop traversal latency\n")
        lines.append("| Platform | p50 (ms) | p95 (ms) | n |")
        lines.append("|---|---|---|---|")
        for pid in PLATFORM_ORDER:
            rw = _read_metric(results.get(pid) or {}, "read_workloads", "traversal", f"hop{hop}")
            if not rw:
                lines.append(f"| {_platform_name(pid)} | — | — | — |")
                continue
            lines.append(f"| {_platform_name(pid)} | {_fmt(rw['p50_ms'])} | {_fmt(rw['p95_ms'])} | {rw['n']} |")
        lines.append("")

    for key, title in (
        ("point_lookup", "Point lookup latency"),
        ("indexed_lookup", "Indexed/filtered lookup latency (WHERE year = ...)"),
        ("aggregation", "Aggregation latency (count of Paper grouped by year)"),
    ):
        lines.append(f"## {title}\n")
        lines.append("| Platform | p50 (ms) | p95 (ms) | n |")
        lines.append("|---|---|---|---|")
        for pid in PLATFORM_ORDER:
            rw = _read_metric(results.get(pid) or {}, "read_workloads", key)
            if not rw:
                lines.append(f"| {_platform_name(pid)} | — | — | — |")
                continue
            lines.append(f"| {_platform_name(pid)} | {_fmt(rw['p50_ms'])} | {_fmt(rw['p95_ms'])} | {rw['n']} |")
        lines.append("")

    lines.append("## Mixed read/write workload (concurrency sweep)\n")
    lines.append(
        f"80% read / 20% write mix ({config.MIXED_WORKLOAD_DURATION_SEC}s per concurrency level, "
        "see README methodology for exact op composition).\n"
    )
    lines.append("| Platform | Concurrency | Valid run | QPS (successful) | Error rate | Workers connected |")
    lines.append("|---|---|---|---|---|---|")
    for pid in PLATFORM_ORDER:
        sweep = _read_metric(results.get(pid) or {}, "mixed_workload_sweep", default={})
        for c in config.CONCURRENCY_LEVELS:
            run = sweep.get(str(c))
            if not run:
                lines.append(f"| {_platform_name(pid)} | {c} | — | — | — | — |")
                continue
            lines.append(
                f"| {_platform_name(pid)} | {c} | {run['run_valid']} | {_fmt(run['qps'], 2)} | "
                f"{_fmt((run.get('error_rate') or 0) * 100, 2)}% | "
                f"{run.get('workers_connected', '—')}/{c} |"
            )
    lines.append("")

    lines.append("## Footprint\n")
    lines.append("| Platform | Nodes | Edges | Notes |")
    lines.append("|---|---|---|---|")
    for pid in PLATFORM_ORDER:
        fp = _read_metric(results.get(pid) or {}, "footprint", default={})
        lines.append(f"| {_platform_name(pid)} | {fp.get('nodes', '—')} | {fp.get('edges', '—')} | {fp.get('note', '')} |")
    lines.append("")

    lines.extend(build_winner_section(winners))

    lines.append("## Charts\n")
    for fname, caption in (
        ("traversal_latency.png", "Traversal latency (p95) by hop depth"),
        ("lookup_latency.png", "Lookup & aggregation latency (p95)"),
        ("latency_stability.png", "p95/p50 ratio — tail latency multiplier per workload"),
        ("ingest_throughput.png", "Ingest throughput"),
        ("mixed_workload_qps.png", "Mixed workload QPS vs. concurrency"),
        ("mixed_workload_error_rate.png", "Mixed workload error rate vs. concurrency"),
    ):
        lines.append(f"![{caption}](charts/{fname})\n")

    lines.append(
        "\nSee also: [`dashboard.html`](dashboard.html) for a browsable summary, "
        "or `results/raw/*.json` for the full per-platform data behind every table above.\n"
    )

    return "\n".join(lines)


# -- charts -----------------------------------------------------------------------
def _grouped_bar(ax, categories: list[str], series: dict[str, list[float | None]], ylabel: str):
    n_platforms = len(series)
    width = 0.8 / max(n_platforms, 1)
    x = range(len(categories))
    for i, (pid, values) in enumerate(series.items()):
        offset = (i - (n_platforms - 1) / 2) * width
        xs = [xi + offset for xi in x]
        vals = [v if v is not None else 0 for v in values]
        ax.bar(xs, vals, width=width * 0.9, label=_platform_name(pid), color=PLATFORM_COLOR.get(pid, "#999999"))
    ax.set_xticks(list(x))
    ax.set_xticklabels(categories)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)
    if series:
        ax.legend(frameon=False, fontsize=8)


def chart_traversal_latency(results: dict) -> None:
    hops = [f"hop{h}" for h in config.HOP_DEPTHS]
    series = {}
    for pid in PLATFORM_ORDER:
        traversal = _read_metric(results.get(pid) or {}, "read_workloads", "traversal", default={})
        vals = [traversal.get(h, {}).get("p95_ms") for h in hops]
        if any(v is not None for v in vals):
            series[pid] = vals
    fig, ax = plt.subplots(figsize=(7, 4))
    _grouped_bar(ax, [f"{h}-hop" for h in config.HOP_DEPTHS], series, "p95 latency (ms)")
    ax.set_title("Traversal latency (p95) by hop depth")
    fig.tight_layout()
    fig.savefig(config.RESULTS_CHARTS / "traversal_latency.png", dpi=150)
    plt.close(fig)


def chart_lookup_latency(results: dict) -> None:
    keys = ["point_lookup", "indexed_lookup", "aggregation"]
    labels = ["Point lookup", "Indexed lookup", "Aggregation"]
    series = {}
    for pid in PLATFORM_ORDER:
        rw = _read_metric(results.get(pid) or {}, "read_workloads", default={})
        vals = [(rw.get(k) or {}).get("p95_ms") for k in keys]
        if any(v is not None for v in vals):
            series[pid] = vals
    fig, ax = plt.subplots(figsize=(7, 4))
    _grouped_bar(ax, labels, series, "p95 latency (ms)")
    ax.set_title("Lookup & aggregation latency (p95)")
    fig.tight_layout()
    fig.savefig(config.RESULTS_CHARTS / "lookup_latency.png", dpi=150)
    plt.close(fig)


def chart_latency_stability(results: dict) -> None:
    """p95/p50 ratio per workload: a database that's usually fast but
    occasionally spikes shows a high ratio here even if its raw p50 looks
    great — the raw p50/p95 numbers themselves are in the tables above."""
    categories = ["point_lookup", "hop1", "hop2", "hop3", "indexed_lookup", "aggregation"]
    labels = ["Point\nlookup", "1-hop", "2-hop", "3-hop", "Indexed\nlookup", "Aggregation"]

    def get_stat(rw: dict, key: str) -> dict | None:
        if key.startswith("hop"):
            return (rw.get("traversal") or {}).get(key)
        return rw.get(key)

    series = {}
    for pid in PLATFORM_ORDER:
        rw = _read_metric(results.get(pid) or {}, "read_workloads", default={})
        vals = []
        has_any = False
        for key in categories:
            stat = get_stat(rw, key)
            if stat and stat.get("p50_ms"):
                vals.append(round(stat["p95_ms"] / stat["p50_ms"], 2))
                has_any = True
            else:
                vals.append(None)
        if has_any:
            series[pid] = vals
    fig, ax = plt.subplots(figsize=(8, 4))
    _grouped_bar(ax, labels, series, "p95 / p50 ratio")
    ax.axhline(1.0, color=GRID_COLOR, linewidth=1, linestyle="--")
    ax.set_title("Latency stability (lower = more consistent, 1.0 = no tail)")
    fig.tight_layout()
    fig.savefig(config.RESULTS_CHARTS / "latency_stability.png", dpi=150)
    plt.close(fig)


def chart_ingest_throughput(results: dict) -> None:
    series = {}
    for pid in PLATFORM_ORDER:
        load = _read_metric(results.get(pid) or {}, "load")
        if load:
            series[pid] = [load.get("nodes_per_sec"), load.get("rels_per_sec")]
    fig, ax = plt.subplots(figsize=(7, 4))
    _grouped_bar(ax, ["Nodes/sec", "Rels/sec"], series, "throughput")
    ax.set_title("Ingest throughput")
    fig.tight_layout()
    fig.savefig(config.RESULTS_CHARTS / "ingest_throughput.png", dpi=150)
    plt.close(fig)


def _mixed_line_chart(results: dict, metric_key: str, ylabel: str, title: str, filename: str, scale: float = 1.0) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    any_series = False
    for pid in PLATFORM_ORDER:
        sweep = _read_metric(results.get(pid) or {}, "mixed_workload_sweep", default={})
        xs, ys = [], []
        for c in config.CONCURRENCY_LEVELS:
            run = sweep.get(str(c))
            if run and run.get("run_valid") and run.get(metric_key) is not None:
                xs.append(c)
                ys.append(run[metric_key] * scale)
        if xs:
            any_series = True
            ax.plot(
                xs, ys, marker="o", markersize=6, linewidth=2,
                label=_platform_name(pid), color=PLATFORM_COLOR.get(pid, "#999999"),
            )
    ax.set_xlabel("Concurrent clients")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(color=GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)
    if any_series:
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(config.RESULTS_CHARTS / filename, dpi=150)
    plt.close(fig)


def chart_mixed_workload_qps(results: dict) -> None:
    _mixed_line_chart(results, "qps", "Successful QPS", "Mixed workload throughput vs. concurrency", "mixed_workload_qps.png")


def chart_mixed_workload_error_rate(results: dict) -> None:
    _mixed_line_chart(
        results, "error_rate", "Error rate (%)", "Mixed workload error rate vs. concurrency",
        "mixed_workload_error_rate.png", scale=100.0,
    )


# -- HTML dashboard -----------------------------------------------------------------
def _stat_card(label: str, value: str, sub: str) -> str:
    return (
        '<div class="card">'
        f'<div class="card-label">{label}</div>'
        f'<div class="card-value">{value}</div>'
        f'<div class="card-sub">{sub}</div>'
        "</div>"
    )


def build_html(results: dict, winners: list[dict]) -> str:
    winners_by_label = {w["workload"]: w for w in winners}

    def card_for(label: str) -> str:
        w = winners_by_label.get(label, {"platform": "—", "value": "no data"})
        return _stat_card(label, w["platform"], w["value"])

    stat_cards = "".join(
        [
            card_for("Point lookup (p95)"),
            card_for(f"{config.HOP_DEPTHS[-1]}-hop traversal (p95)"),
            card_for("Ingest throughput (rels/sec)"),
            card_for(f"Mixed workload QPS @ {config.CONCURRENCY_LEVELS[-1]} clients"),
        ]
    )

    winner_rows = "".join(
        f"<tr><td>{w['workload']}</td><td>{w['platform']}</td><td>{w['value']}</td></tr>" for w in winners
    )

    status_rows = "".join(
        f"<tr><td>{_platform_name(pid)}</td><td>{(results.get(pid) or {}).get('status', 'not run')}</td>"
        f"<td>{(results.get(pid) or {}).get('timestamp_utc', '—')}</td></tr>"
        for pid in PLATFORM_ORDER
    )

    chart_imgs = "".join(
        f'<figure><img src="charts/{fname}" alt="{caption}"><figcaption>{caption}</figcaption></figure>'
        for fname, caption in (
            ("traversal_latency.png", "Traversal latency (p95) by hop depth"),
            ("lookup_latency.png", "Lookup & aggregation latency (p95)"),
            ("latency_stability.png", "Latency stability (p95/p50 ratio)"),
            ("ingest_throughput.png", "Ingest throughput"),
            ("mixed_workload_qps.png", "Mixed workload QPS vs. concurrency"),
            ("mixed_workload_error_rate.png", "Mixed workload error rate vs. concurrency"),
        )
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CognoDB Benchmark Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    color-scheme: light dark;
    --surface: #fcfcfb; --surface-2: #f2f1ec; --text: #0b0b0b; --text-sec: #52514e; --border: #d8d7d0;
    --accent: #2a78d6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --surface: #1a1a19; --surface-2: #242422; --text: #ffffff; --text-sec: #c3c2b7; --border: #3a3a37; --accent: #3987e5; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 2rem 1.25rem 4rem; background: var(--surface); color: var(--text);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 0.25rem; }}
  .subtitle {{ color: var(--text-sec); margin-bottom: 1.5rem; }}
  .banner {{
    background: var(--surface-2); border: 1px solid var(--border); border-left: 4px solid var(--accent);
    border-radius: 6px; padding: 0.9rem 1.1rem; margin-bottom: 2rem; color: var(--text-sec); font-size: 0.92rem;
  }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem; margin-bottom: 2.5rem; }}
  .card {{ background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 1rem 1.1rem; }}
  .card-label {{ font-size: 0.78rem; color: var(--text-sec); text-transform: uppercase; letter-spacing: 0.03em; }}
  .card-value {{ font-size: 1.35rem; font-weight: 600; margin: 0.2rem 0; }}
  .card-sub {{ font-size: 0.85rem; color: var(--text-sec); }}
  h2 {{ font-size: 1.15rem; margin-top: 2.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--text-sec); font-weight: 600; }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.5rem; margin-top: 1rem; }}
  figure {{ margin: 0; background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; padding: 0.75rem; }}
  figure img {{ width: 100%; height: auto; border-radius: 6px; display: block; }}
  figcaption {{ font-size: 0.85rem; color: var(--text-sec); margin-top: 0.5rem; }}
  footer {{ margin-top: 3rem; color: var(--text-sec); font-size: 0.85rem; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>CognoDB Cloud Benchmark</h1>
  <div class="subtitle">Quick view — generated by <code>make report</code>. For the reproducible source of truth, see <a href="RESULTS.md">RESULTS.md</a> and <code>results/raw/*.json</code>.</div>
  <div class="banner">{FAIRNESS_BANNER}</div>

  <div class="cards">{stat_cards}</div>

  <h2>Winner by workload</h2>
  <table>
    <thead><tr><th>Workload</th><th>Winner</th><th>Value</th></tr></thead>
    <tbody>{winner_rows}</tbody>
  </table>
  <p style="color:var(--text-sec);font-size:0.85rem;">These are winners only for this specific dataset, query set, and resource-capped setup — not a general claim about which database is "best."</p>

  <h2>Run status</h2>
  <table>
    <thead><tr><th>Platform</th><th>Status</th><th>Timestamp (UTC)</th></tr></thead>
    <tbody>{status_rows}</tbody>
  </table>

  <h2>Charts</h2>
  <div class="charts">{chart_imgs}</div>

  <footer>Generated by <code>scripts/generate_report.py</code> from <code>results/raw/*.json</code>. Do not hand-edit this file.</footer>
</div>
</body>
</html>
"""


def main() -> None:
    results = load_latest_results()
    winners = compute_winners(results)

    md = build_markdown(results, winners)
    with open(config.ROOT / "results" / "RESULTS.md", "w") as f:
        f.write(md)

    chart_traversal_latency(results)
    chart_lookup_latency(results)
    chart_latency_stability(results)
    chart_ingest_throughput(results)
    chart_mixed_workload_qps(results)
    chart_mixed_workload_error_rate(results)

    html = build_html(results, winners)
    with open(config.ROOT / "results" / "dashboard.html", "w") as f:
        f.write(html)

    print(f"Wrote {config.ROOT / 'results' / 'RESULTS.md'}")
    print(f"Wrote {config.ROOT / 'results' / 'dashboard.html'}")
    print(f"Wrote charts to {config.RESULTS_CHARTS}")


if __name__ == "__main__":
    main()
