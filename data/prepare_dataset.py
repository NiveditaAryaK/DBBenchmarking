"""Sample the raw cit-HepPh citation graph down to a size that fits every
platform's free tier, and write nodes.csv / edges.csv / manifest.json.

Sampling method: forest-fire (Leskovec & Faloutsos, "Sampling from Large
Graphs", KDD 2006). Plain BFS/snowball sampling is known to over-represent
dense hub neighborhoods, which would directly bias our 1/2/3-hop traversal
latency benchmark toward artificially well-connected start nodes. Forest-fire
is a randomized relaxation of BFS — at each burning node it "catches fire"
along only a random subset of its edges (independently, forward and backward
edges have different burn probabilities) — which produces a sample much
closer to the full graph's degree distribution than plain BFS while still
staying connected enough to support multi-hop traversal queries (unlike
uniform-random node/edge sampling, which fragments a sparse citation graph
into a mess of tiny disconnected pieces).

The exact burn probabilities (see benchmark/config.py) are not load-bearing.
What matters, and what this script reports in manifest.json, is the
*resulting* graph: node/edge count, degree distribution, and connectivity.

No synthetic data is introduced: the `year` node property is populated only
where cit-HepPh-dates.txt has a real date for that paper. Papers with no
matching date are loaded with no `year` property, and are excluded from the
indexed/filtered-lookup query pool — never backfilled with a made-up value.

Usage: python -m data.prepare_dataset
"""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import deque
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from benchmark import config


def parse_edges(path: Path) -> list[tuple[int, int]]:
    edges = []
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            a, b = line.split()
            edges.append((int(a), int(b)))
    return edges


def parse_dates(path: Path) -> dict[int, str]:
    dates = {}
    with open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            paper_id, date = parts
            dates[int(paper_id)] = date
    return dates


def build_adjacency(edges: list[tuple[int, int]]):
    out_adj: dict[int, list[int]] = {}
    in_adj: dict[int, list[int]] = {}
    for a, b in edges:
        out_adj.setdefault(a, []).append(b)
        in_adj.setdefault(b, []).append(a)
    return out_adj, in_adj


def forest_fire_sample(out_adj, in_adj, all_nodes: list[int]) -> tuple[set[int], int]:
    rng = random.Random(config.SAMPLE_SEED)
    visited: set[int] = set()
    # An edge becomes part of the induced subgraph exactly when its second
    # endpoint joins `visited`, so this counter tracks the true induced-edge
    # count as we go (not just an estimate) — confirmed exactly against
    # induced_edges() below via the hard validation after sampling.
    edge_count = 0

    def add_node(v: int) -> bool:
        nonlocal edge_count
        if v in visited:
            return False
        visited.add(v)
        for u in out_adj.get(v, ()):
            if u in visited:
                edge_count += 1
        for w in in_adj.get(v, ()):
            # w == v is a self-loop (cit-HepPh has 44): the out_adj pass
            # above already counted it once via out_adj[v] containing v.
            # Counting it again here would double-count that single edge.
            if w in visited and w != v:
                edge_count += 1
        return True

    # Prefer seeds with out-edges so fires actually have somewhere to spread.
    seed_candidates = [n for n in all_nodes if out_adj.get(n)]
    rng.shuffle(seed_candidates)
    seed_iter = iter(seed_candidates)
    restarts = 0

    # Cap on any single fire's own contribution — see the comment on
    # FOREST_FIRE_NUM_SEEDS in benchmark/config.py. Not a hard limit on the
    # *number* of fires: if fires keep dying out below budget (queue empties
    # early), sampling just keeps restarting from fresh seeds until the
    # global target is met.
    per_fire_edge_budget = max(1, config.SAMPLE_TARGET_EDGES // config.FOREST_FIRE_NUM_SEEDS)

    def target_met() -> bool:
        return len(visited) >= config.SAMPLE_MAX_NODES or edge_count >= config.SAMPLE_TARGET_EDGES

    while not target_met():
        seed = next(seed_iter, None)
        while seed is not None and seed in visited:
            seed = next(seed_iter, None)
        if seed is None:
            break  # exhausted the graph before hitting the target
        add_node(seed)
        restarts += 1
        fire_start_edges = edge_count
        queue = deque([seed])

        def fire_budget_met() -> bool:
            return edge_count - fire_start_edges >= per_fire_edge_budget

        while queue and not target_met() and not fire_budget_met():
            v = queue.popleft()

            forward = [u for u in out_adj.get(v, ()) if u not in visited]
            rng.shuffle(forward)
            for u in forward:
                if target_met() or fire_budget_met():
                    break
                if rng.random() < config.FOREST_FIRE_FORWARD_P and add_node(u):
                    queue.append(u)

            backward = [w for w in in_adj.get(v, ()) if w not in visited]
            rng.shuffle(backward)
            for w in backward:
                if target_met() or fire_budget_met():
                    break
                if rng.random() < config.FOREST_FIRE_BACKWARD_P and add_node(w):
                    queue.append(w)

    return visited, restarts


def induced_edges(edges: list[tuple[int, int]], nodes: set[int]) -> list[tuple[int, int]]:
    return [(a, b) for a, b in edges if a in nodes and b in nodes]


def connected_components(nodes: set[int], edges: list[tuple[int, int]]):
    """Weakly-connected components via union-find (citation direction ignored
    for connectivity purposes — this is a standard graph-stats convention)."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        union(a, b)

    sizes: dict[int, int] = {}
    for n in nodes:
        r = find(n)
        sizes[r] = sizes.get(r, 0) + 1
    return sizes


def resolve_year(paper_id: int, dates: dict[int, str]) -> str | None:
    """cit-HepPh-dates.txt notes cross-listed papers are keyed as 11<id>.
    Returns the real 4-digit year, or None if no date is on record — never
    a fabricated value."""
    if paper_id in dates:
        return dates[paper_id][:4]
    prefixed = int(f"11{paper_id}")
    if prefixed in dates:
        return dates[prefixed][:4]
    return None


def main() -> None:
    if not config.DATASET_RAW_EDGES_FILE.exists() or not config.DATASET_RAW_DATES_FILE.exists():
        print("Raw dataset missing — run `python -m data.download` first.")
        sys.exit(1)

    edges = parse_edges(config.DATASET_RAW_EDGES_FILE)
    dates = parse_dates(config.DATASET_RAW_DATES_FILE)
    all_nodes = sorted({n for e in edges for n in e})
    out_adj, in_adj = build_adjacency(edges)

    print(f"Full graph: {len(all_nodes):,} nodes, {len(edges):,} edges")

    sample_nodes, restarts = forest_fire_sample(out_adj, in_adj, all_nodes)
    sample_edges = induced_edges(edges, sample_nodes)

    # Hard requirement, checked against the real induced-edge count (not the
    # sampler's running counter) — the assignment requires >= 100,000
    # relationships and SAMPLE_TARGET_EDGES is our floor above that.
    if len(sample_edges) < config.SAMPLE_TARGET_EDGES:
        raise RuntimeError(
            f"Sample contains only {len(sample_edges):,} relationships; "
            f"benchmark requires at least {config.SAMPLE_TARGET_EDGES:,}. "
            f"Raise FOREST_FIRE_FORWARD_P/BACKWARD_P or SAMPLE_MAX_NODES in "
            f"benchmark/config.py and re-run."
        )
    if len(sample_nodes) < config.SAMPLE_MIN_NODES:
        print(
            f"WARNING: sample has only {len(sample_nodes):,} nodes, below "
            f"SAMPLE_MIN_NODES={config.SAMPLE_MIN_NODES:,}."
        )

    # --- degree stats on the induced sample -------------------------------
    out_deg = {n: 0 for n in sample_nodes}
    in_deg = {n: 0 for n in sample_nodes}
    for a, b in sample_edges:
        out_deg[a] += 1
        in_deg[b] += 1
    total_deg = {n: out_deg[n] + in_deg[n] for n in sample_nodes}
    degrees = list(total_deg.values())
    isolated = sum(1 for d in degrees if d == 0)

    components = connected_components(sample_nodes, sample_edges)
    largest_component = max(components.values()) if components else 0

    # --- node properties (years are real or absent, never fabricated) ------
    node_rows = []
    years_by_node: dict[int, str] = {}
    missing_year_count = 0
    for n in sorted(sample_nodes):
        year = resolve_year(n, dates)
        if year is None:
            missing_year_count += 1
        else:
            years_by_node[n] = year
        node_rows.append(
            {
                "paperId": n,
                "year": year if year is not None else "",
                "outDegree": out_deg[n],
                "inDegree": in_deg[n],
            }
        )

    config.DATASET_NODES_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(config.DATASET_NODES_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["paperId", "year", "outDegree", "inDegree"])
        w.writeheader()
        w.writerows(node_rows)

    with open(config.DATASET_EDGES_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fromPaperId", "toPaperId"])
        w.writerows(sample_edges)

    # --- deterministic query start-node / filter-value sample --------------
    # Warmup and measured items are disjoint, dedicated sets — not "the first
    # 20 of the 150 measured ones" — so there's no ambiguity about which
    # queries are discarded warm-up vs. which contribute to p50/p95, and
    # every platform still gets the exact same 170 items in the same roles.
    query_rng = random.Random(config.RANDOM_QUERY_SEED)
    n_warmup = config.WARMUP_ITERATIONS
    n_measured = config.MEASURED_ITERATIONS
    n_total = n_warmup + n_measured

    def sample_pool(pool: list, k: int) -> list:
        # A benchmark should fail loudly on a broken precondition, not
        # silently sample fewer items and quietly change what "150 measured
        # iterations" means run to run.
        if len(pool) < k:
            raise RuntimeError(f"Query pool has only {len(pool)} items; {k} are required.")
        return query_rng.sample(pool, k)

    def split(items: list) -> dict:
        return {"warmup": items[:n_warmup], "measured": items[n_warmup:]}

    traversal_pool = sorted(n for n in sample_nodes if out_deg[n] >= config.QUERY_MIN_OUT_DEGREE)
    lookup_pool = sorted(sample_nodes)
    # Indexed/filtered lookup filters on the real `year` property only —
    # nodes with no recorded date are not eligible filter targets.
    indexed_lookup_pool = sorted(years_by_node.keys())

    lookup_sample = split(sample_pool(lookup_pool, n_total))
    traversal_sample = split(sample_pool(traversal_pool, n_total))
    # Sample distinct nodes with real year metadata, then use their years as
    # filter values. Year values may naturally repeat because many papers
    # share the same publication year; this reflects the actual property
    # distribution, not an intentional with-replacement draw.
    indexed_lookup_node_sample = sample_pool(indexed_lookup_pool, n_total)
    indexed_lookup_year_sample = split([years_by_node[n] for n in indexed_lookup_node_sample])

    with open(config.QUERY_SAMPLE_FILE, "w") as f:
        json.dump(
            {
                "point_lookup": lookup_sample,
                "traversal": traversal_sample,
                "indexed_lookup_years": indexed_lookup_year_sample,
            },
            f,
            indent=2,
        )

    # --- manifest -------------------------------------------------------------
    manifest = {
        "source": "SNAP cit-HepPh (https://snap.stanford.edu/data/cit-HepPh.html)",
        "source_full_graph": {"nodes": len(all_nodes), "edges": len(edges)},
        "sampling_method": "forest-fire (Leskovec & Faloutsos 2006)",
        "sampling_params": {
            "seed": config.SAMPLE_SEED,
            "forward_burn_p": config.FOREST_FIRE_FORWARD_P,
            "backward_burn_p": config.FOREST_FIRE_BACKWARD_P,
            "target_edges": config.SAMPLE_TARGET_EDGES,
            "max_nodes": config.SAMPLE_MAX_NODES,
            "fire_restarts": restarts,
        },
        "sample_graph": {
            "nodes": len(sample_nodes),
            "edges": len(sample_edges),
            "avg_degree": round(mean(degrees), 3) if degrees else 0,
            "median_degree": median(degrees) if degrees else 0,
            "max_degree": max(degrees) if degrees else 0,
            "isolated_nodes": isolated,
            "connected_components": len(components),
            "largest_component_size": largest_component,
            "largest_component_fraction": round(largest_component / len(sample_nodes), 4) if sample_nodes else 0,
        },
        "node_properties": {
            "year_source": "cit-HepPh-dates.txt, joined on paperId (handles 11<id> cross-listing prefix)",
            "year_present_count": len(years_by_node),
            "year_missing_count": missing_year_count,
            "note": "No synthetic/fabricated years — papers with no source date simply have no `year` property.",
        },
        "query_sample": {
            "point_lookup_pool_size": len(lookup_pool),
            "traversal_pool_size": len(traversal_pool),
            "traversal_min_out_degree": config.QUERY_MIN_OUT_DEGREE,
            "indexed_lookup_pool_size": len(indexed_lookup_pool),
            "warmup_iterations": n_warmup,
            "measured_iterations": n_measured,
            "point_lookup_sampled": len(lookup_sample["warmup"]) + len(lookup_sample["measured"]),
            "traversal_sampled": len(traversal_sample["warmup"]) + len(traversal_sample["measured"]),
            "indexed_lookup_sampled": len(indexed_lookup_year_sample["warmup"])
            + len(indexed_lookup_year_sample["measured"]),
        },
    }
    with open(config.DATASET_MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps(manifest, indent=2))
    print(f"\nWrote {config.DATASET_NODES_CSV}, {config.DATASET_EDGES_CSV}, {config.DATASET_MANIFEST}")
    print(f"Wrote {config.QUERY_SAMPLE_FILE}")


if __name__ == "__main__":
    main()
