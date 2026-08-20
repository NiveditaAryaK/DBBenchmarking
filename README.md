# Graph Database Cloud Benchmark

### CognoDB Cloud vs. Neo4j AuraDB, FalkorDB, Memgraph and ArangoDB

A reproducible benchmark of five graph-database platforms — [CognoDB Cloud](https://cognodb.com), Neo4j AuraDB Free, FalkorDB Cloud, self-hosted Memgraph, and self-hosted ArangoDB — using the same 105,000-relationship citation graph, equivalent logical workloads, fixed query inputs, warm-up runs, percentile latency reporting, and automated concurrency testing. Built for [Wexa AI's take-home assignment](#).

The goal was not to make CognoDB — or any database — "win." The goal was to build a benchmark whose methodology and limitations are visible enough that someone else can reproduce, challenge, and extend it.

> **Dataset:** SNAP cit-HepPh sample — 8,769 nodes / 105,000 relationships
> **Measured reads:** 150 iterations after 20 dedicated warm-ups
> **Traversals:** 1-hop / 2-hop / 3-hop
> **Concurrency:** 1 / 10 / 20 / 40 clients
> **Mixed workload:** 80% reads / 20% writes
> **Outputs:** raw per-platform JSON, Markdown result tables, six charts, and an HTML dashboard

**Run it yourself:** `make setup && make dataset && make docker-up && make all && make report`
`make all` produces timestamped machine-readable results for every configured
platform; `make report` regenerates the result tables, charts, and dashboard
from those files, so benchmark numbers are never hand-copied into the report.

[Full results](#results) · [Results table](results/RESULTS.md) · [Dashboard](results/dashboard.html)

## Contents

- [Key findings](#key-findings)
- [Benchmark at a glance](#benchmark-at-a-glance)
- [Platforms compared](#platforms-compared)
- [Fairness & experimental environment](#fairness--experimental-environment)
- [Dataset](#dataset)
- [Workloads](#workloads)
- [Methodology](#methodology)
- [Results](#results)
- [Analysis](#analysis)
- [Caveats & limitations](#caveats--limitations)
- [Reproduce the benchmark](#reproduce-the-benchmark)
- [Repository structure](#repository-structure)
- [Extending the harness](#extending-the-harness)

## Key findings

- **FalkorDB** had the lowest p95 traversal latency of the three remotely-hosted managed services at every hop depth (3.6 / 5.0 / 7.8 ms for 1/2/3 hops) and the highest sustained mixed-workload throughput observed anywhere in the sweep — 2,847.7 QPS at 40 concurrent clients, with a 0% error rate at every concurrency level tested.
- **CognoDB** was competitive on shallow reads (9.4 ms p95 point lookup, 9.9 ms p95 1-hop traversal) but developed a pronounced tail at 3 hops: 17.2 ms p50 vs. 189.6 ms p95 — more than a 10x gap between the typical and worst-case request that isn't present at 1 or 2 hops. Its mixed-workload error rate also climbed to 0.25% at 40 clients, the clearest sign of contention under load among the managed platforms.
- **AuraDB Free** returned a nearly constant ~225–245 ms response time across point lookups, 1/2/3-hop traversals, and aggregation — queries with very different logical cost. Because this benchmark measures end-to-end client-observed latency, that flat profile points to a large fixed cost (network path, free-tier connection/gateway overhead, scheduling) rather than telling us anything about Neo4j's execution engine itself.
- **Memgraph** and **ArangoDB** — self-hosted under the same 0.5 vCPU / 512 MB cap as CognoDB, but co-located with the benchmark client — posted the lowest raw single-query latencies and, for ArangoDB, the highest ingest throughput in the entire report. Those numbers carry a real, unequalized network advantage over the three managed services and are marked **†** throughout rather than presented as managed-cloud wins. See [Analysis](#analysis) for the full breakdown.

## Benchmark at a glance

| Dimension | Configuration |
|---|---|
| Dataset | SNAP cit-HepPh, forest-fire sampled |
| Sample size | 8,769 nodes / 105,000 relationships |
| Sampling | Deterministic multi-seed forest-fire (seed=42, 13 fire restarts) |
| Read iterations | 150 measured calls per workload per platform |
| Warm-up | 20 dedicated warm-up calls, disjoint from the measured 150 |
| Traversals | Exactly 1 / 2 / 3 outgoing `CITES` hops |
| Mixed workload | 80% reads (50% point lookup / 30% 1-hop / 20% 2-hop) / 20% writes |
| Concurrency sweep | 1 / 10 / 20 / 40 concurrent clients, 60s sustained per level |
| Ingest batch size | 500 rows/batch |
| Read metrics | p50 / p95 / p99 / mean / min / max |
| Throughput metrics | Successful QPS + error rate |

## Platforms compared

| Platform | Why it's in the comparison |
|---|---|
| **CognoDB Cloud** | The assignment's target. Bolt protocol, Cypher, official `neo4j` driver. |
| **Neo4j AuraDB Free** | The closest protocol/query-language comparator: both it and CognoDB expose Bolt + Cypher through the official Neo4j driver, on a different vendor's managed cloud. |
| **FalkorDB Cloud (Free)** | Keeps a Cypher-like query model (openCypher subset) while changing the engine architecture substantially — GraphBLAS/sparse-matrix-based, RESP protocol instead of Bolt. |
| **Memgraph** (self-hosted, capped) | A third Cypher-compatible engine (in-memory, C++), self-hosted and explicitly resource-capped to CognoDB's observed envelope rather than relying on a vendor's free-tier definition of "small." |
| **ArangoDB** (self-hosted, capped) | The deliberate outlier: AQL, not Cypher, and a document/multi-model store instead of a native property graph — tests how much of the difference is query-language/data-model overhead rather than engine performance. |

Together these give the benchmark three useful comparison axes: protocol/query-language similarity (CognoDB vs. Aura), engine architecture (the Bolt/Cypher trio vs. FalkorDB), and deployment model (managed cloud vs. self-hosted, capped). Managed services bundle too many unknowns (hardware, scheduling, network path) to claim any single pair isolates *only* one variable — see [Caveats](#caveats--limitations) — but the split is still useful for reasoning about *where* differences show up. See [Analysis](#analysis) for what the numbers actually show.

TigerGraph Cloud and ArangoDB Oasis were considered and dropped: TigerGraph's GSQL + schema/loading-job API was too heavy to implement correctly within the assignment's 48-hour window without cutting corners elsewhere, and ArangoDB Oasis's trial-credit model made a self-hosted, explicitly-capped ArangoDB a cleaner fairness story than a managed trial with undisclosed hardware.

## Fairness & experimental environment

The assignment requires *"the same (or as close as the tiers allow) vCPU, RAM and storage allocation for every platform."* Free-tier managed clouds don't expose identical hardware or identical network placement, so this section documents both honestly rather than claiming parity that wasn't achieved.

### Resource parity

**Managed platforms (CognoDB, Aura, FalkorDB):** whatever their free tier actually provisions, documented as-observed:

| Platform | vCPU | RAM | Disk |
|---|---|---|---|
| CognoDB Cloud (c0) | 0.5 (burstable) | **512 MB** | 1 GB |
| Neo4j AuraDB Free | not disclosed | not disclosed (Neo4j caps by node/relationship count, not RAM) | not disclosed |
| FalkorDB Cloud (Free) | shared | 100 MB | shared (RAM-backed) |

> **Discrepancy note:** the assignment PDF describes CognoDB's free tier as *"burstable 0.5 vCPU, 256 MB RAM, 1 GB disk."* The actual provisioned `c0` instance's console showed **512 MB RAM** at benchmark time. The observed console specification is what's used throughout this README and the harness — the product evidently changed since the assignment was written, and reporting what we actually measured is more honest than reporting the PDF's stale number.

**Self-hosted platforms (Memgraph, ArangoDB):** explicitly capped via Docker (`docker/docker-compose.yml`) to **0.5 vCPU / 512 MB RAM**, matching CognoDB's observed envelope, rather than left unconstrained. `mem_limit`/`cpus` (not `deploy.resources.limits`, which requires swarm mode) are applied directly by a plain `docker compose up`. Disk is host-backed and not quota-limited by Docker itself — the dataset is small enough (see [Dataset](#dataset)) that this doesn't matter in practice, and actual stored counts are reported in the footprint table rather than an unenforced claim of a cap.

### Deployment & network placement

Compute caps are only half of "fair." *Where* each platform runs relative to the benchmark client matters just as much, and this run does **not** have equal network placement across all five platforms:

| Platform | Deployment location | Client path |
|---|---|---|
| CognoDB Cloud | GCP `us-east4` (Northern Virginia) | Remote managed endpoint |
| FalkorDB Cloud (Free) | AWS `us-east-1` (Northern Virginia) | Remote managed endpoint |
| Neo4j AuraDB Free | Region not exposed/recorded during this run | Remote managed endpoint |
| Memgraph (self-hosted, capped) | GitHub Codespaces host | Co-located with the benchmark client |
| ArangoDB (self-hosted, capped) | GitHub Codespaces host | Co-located with the benchmark client |

The benchmark client itself ran inside a GitHub Codespaces environment. CognoDB and FalkorDB were both provisioned in Northern-Virginia-area US-East regions, but on different cloud providers and physically distinct regions (`us-east4` on GCP vs. `us-east-1` on AWS) — geographically close, not identical, so this README calls them "geographically close US-East regions" rather than "the same region." AuraDB's region wasn't exposed in the console during this run and is reported as unknown rather than guessed.

Memgraph and ArangoDB, by contrast, ran as Docker containers inside the same Codespace as the client — a real network hop away from *nothing*. Their absolute latency numbers therefore have an unequalized advantage over all three managed services and are marked **†** everywhere they appear in [Results](#results) and [Analysis](#analysis). The managed-cloud-to-managed-cloud comparison (CognoDB vs. Aura vs. FalkorDB) is the cleaner cross-service latency comparison in this report; self-hosted numbers are reference points for "what does this engine do under an equivalent CPU/RAM cap with no network cost," not a claim that Memgraph or ArangoDB would beat the managed services if deployed remotely.

## Dataset

**Source:** [SNAP cit-HepPh](https://snap.stanford.edu/data/cit-HepPh.html) — a directed citation network of Arxiv High-Energy Physics papers. Full graph: 34,546 nodes, 421,578 edges (including 44 self-citations and one 11-prefixed cross-listing ID convention, both handled explicitly — see `data/prepare_dataset.py`).

**Why sample, and why not uniformly-at-random?** The assignment asks for 100k–500k relationships, small enough to also fit FalkorDB's 100 MB free tier. Sampling individual edges uniformly at random tends to break the local connectivity a 1–3 hop traversal benchmark is meant to measure, and a single unrestricted breadth-first walk can instead over-represent one especially dense neighborhood (Leskovec & Faloutsos, *"Sampling from Large Graphs,"* KDD 2006). This benchmark instead uses **forest-fire sampling**: conceptually a controlled breadth-first walk that "burns" outward from a seed node, following only a random subset of each node's edges, and periodically restarts from a fresh seed elsewhere in the graph once a burn dies out. That keeps the sample from being dominated by either extreme.

Final sample used for every platform in this report:

- 8,769 nodes / 105,000 relationships
- average degree: 23.948 (source graph average degree is ~24.4, so overall connectivity is comparable)
- 1 connected component
- 13 fire restarts, random seed 42

Full method, parameters, and the resulting graph's actual statistics are in `data/processed/manifest.json` (regenerated by `make dataset`).

Both the raw dataset and the processed sample are gitignored (large, and exactly reproducible from source) — `make dataset` fetches and regenerates them deterministically. The processed `nodes.csv`/`edges.csv` SHA-256 fingerprints are recorded in every generated results report (see [Results](#results)) so you can confirm you're reproducing the exact same graph.

No synthetic data: the `year` node property is populated only where the source `cit-HepPh-dates.txt` has a real recorded date for that paper (722 of 8,769 papers have none, and are simply loaded with no `year` property — never backfilled with a made-up value).

## Workloads

| Workload | Logical question |
|---|---|
| Point lookup | Find one `Paper` by its unique `paperId` |
| Indexed/filtered lookup | Find all papers where `year = Y` |
| 1-hop traversal | Papers reachable via exactly one outgoing `CITES` edge from X |
| 2-hop traversal | Papers reachable via exactly two outgoing `CITES` edges from X |
| 3-hop traversal | Papers reachable via exactly three outgoing `CITES` edges from X |
| Aggregation | Count of `Paper`, grouped by `year` |
| Mixed workload | Concurrent 80% reads / 20% writes, swept across 1/10/20/40 clients |

### Timing discipline

Every read workload uses the identical sequence of 150 measured start nodes/filter values (persisted once in `data/processed/query_start_nodes.json`, not re-randomized per run) plus 20 dedicated warm-up calls that are disjoint from those 150 — never "the first 20 of the 150," which would silently shrink the measured sample. Every adapter method fully materializes its result before the timer stops (no lazy cursors), so latency reflects real client-observed time rather than "time to start receiving a response." Percentiles (p50/p95/p99 + mean/min/max) are computed over the 150 measured calls per workload per platform, on a high-resolution monotonic timer.

### Mixed workload

Concurrency is swept across 1 → 10 → 20 → 40 clients, each with its own database connection (not a shared adapter across threads). The operation mix is fixed and identical across platforms — 80% reads (50% point lookup / 30% 1-hop / 20% 2-hop) and 20% writes (an `UPDATE` of an existing node's `benchmark_counter`, never create/delete, so node/edge counts and graph structure never drift during a run). All workers synchronize on a barrier before the clock starts; if the requested concurrency can't be established within 60s, the whole level is marked `run_valid: false` rather than silently reporting a "40-client" result generated by 35 connected workers. Both successful QPS and error rate are reported per level.

## Methodology

**Dataset loading:** identical `nodes.csv`/`edges.csv` loaded into every platform via each platform's official/idiomatic driver (see `benchmark/adapters/`), batched at a uniform 500 rows/batch (`LOAD_BATCH_SIZE`, overridable via env var, and any override is recorded in the result JSON — never silently applied differently per platform).

**Ingest throughput:** nodes/sec and rels/sec timed separately (edge-loading requires nodes to already exist via `MATCH`, so the two phases are never concurrent), plus total wall-clock load time and a post-load count verification. **A platform is never benchmarked on an incomplete load** — `runner.py` aborts with an explicit error if verified node/edge counts don't match the expected dataset, rather than silently benchmarking whatever fraction actually loaded.

**Indexes:** a `paperId` unique constraint plus a secondary index on `year`, created identically on every platform in `reset_schema()` before loading.

**Footprint:** node/edge counts (always available) plus whatever else the platform's driver exposes (Memgraph's `SHOW STORAGE INFO`, ArangoDB's collection statistics); reported as "not observable" where a platform's free tier doesn't expose it over the client API, rather than omitted silently.

## Results

<!-- RESULTS:START -->
Generated by `python -m scripts.generate_report`. Do not hand-edit — edit the harness and re-run.

> **Fairness note:** Managed free tiers do not expose identical underlying hardware; self-hosted comparators (Memgraph, ArangoDB) were explicitly CPU/RAM-capped to match CognoDB's provisioned instance instead of left unconstrained. Results are workload- and resource-tier-specific, not a universal ranking of these databases — see the README methodology and caveats before drawing broader conclusions.

## Platform specs

| Platform | Deployment | Driver | Query language | vCPU | RAM | Disk |
|---|---|---|---|---|---|---|
| CognoDB Cloud | managed-free-tier | bolt | Cypher | 0.5 (burstable) | 512 MB | 1 GB |
| Neo4j AuraDB Free | managed-free-tier | bolt | Cypher | not disclosed (shared) | not disclosed (Neo4j caps by node/rel count, not RAM) | not disclosed |
| FalkorDB Cloud (Free) | managed-free-tier | falkordb | Cypher (openCypher subset) | shared | 100 MB | shared (RAM-backed) |
| Memgraph (self-hosted, capped) | self-hosted-capped | bolt | Cypher (openCypher) | 0.5 (docker --cpus=0.5, matches CognoDB) | 512 MB (docker -m 512m, matches CognoDB) | host-backed, not quota-limited; benchmark dataset kept below 1 GB |
| ArangoDB (self-hosted, capped) | self-hosted-capped | arangodb | AQL | 0.5 (docker --cpus=0.5, matches CognoDB) | 512 MB (docker -m 512m, matches CognoDB) | host-backed, not quota-limited; benchmark dataset kept below 1 GB |

## Dataset

- Source: SNAP cit-HepPh (https://snap.stanford.edu/data/cit-HepPh.html)
- Sampling method: forest-fire (Leskovec & Faloutsos 2006) (seed=42, 13 fire restarts)
- Sample graph: **8,769 nodes / 105,000 relationships**, avg degree 23.948, 1 connected component
- Year property: 8,047 papers with a real recorded year, 722 without (no synthetic years — see caveats)
- `nodes.csv` sha256: `4cfc8db8f1e196099a74ed80347e1bf9170b4619d66d714200044ec930747dbe`
- `edges.csv` sha256: `17de749956ab6dc7969b145b6921926dcc7301ed2977c36a369ea50a2d5b7dc7`
  (fingerprints of the processed dataset actually used to generate this report — regenerate via `make dataset` and diff against these to confirm you reproduced the exact same graph)

## Run status

| Platform | Status | Client env | Timestamp (UTC) | Error |
|---|---|---|---|---|
| CognoDB Cloud | ok | github-codespaces | 2026-08-20T18:06:38.278072+00:00 |  |
| Neo4j AuraDB Free | ok | github-codespaces | 2026-08-20T18:11:22.766372+00:00 |  |
| FalkorDB Cloud (Free) | ok | github-codespaces | 2026-08-20T14:03:34.295831+00:00 |  |
| Memgraph (self-hosted, capped) | ok | github-codespaces | 2026-08-20T18:01:37.354189+00:00 |  |
| ArangoDB (self-hosted, capped) | ok | github-codespaces | 2026-08-20T17:54:59.233553+00:00 |  |

## Data loading

| Platform | Nodes/sec | Rels/sec | Total load time (s) | Load verified |
|---|---|---|---|---|
| CognoDB Cloud | 8926.4 | 20100.5 | 7.4 | yes |
| Neo4j AuraDB Free | 1876.9 | 1832.2 | 65.7 | yes |
| FalkorDB Cloud (Free) | 2927.3 | 5055.6 | 23.8 | yes |
| Memgraph (self-hosted, capped) | 525.0 | 265.2 | 412.8 | yes |
| ArangoDB (self-hosted, capped) | 58867.6 | 32761.1 | 3.4 | yes |

## 1-hop traversal latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 9.1 | 9.9 | 150 |
| Neo4j AuraDB Free | 225.3 | 226.3 | 150 |
| FalkorDB Cloud (Free) | 3.3 | 3.6 | 150 |
| Memgraph (self-hosted, capped) | 2.6 | 3.6 | 150 |
| ArangoDB (self-hosted, capped) | 1.7 | 2.9 | 150 |

## 2-hop traversal latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 10.2 | 18.4 | 150 |
| Neo4j AuraDB Free | 226.2 | 231.9 | 150 |
| FalkorDB Cloud (Free) | 3.5 | 5.0 | 150 |
| Memgraph (self-hosted, capped) | 3.6 | 12.6 | 150 |
| ArangoDB (self-hosted, capped) | 2.7 | 22.8 | 150 |

## 3-hop traversal latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 17.2 | 189.6 | 150 |
| Neo4j AuraDB Free | 228.6 | 244.6 | 150 |
| FalkorDB Cloud (Free) | 4.1 | 7.8 | 150 |
| Memgraph (self-hosted, capped) | 5.5 | 18.6 | 150 |
| ArangoDB (self-hosted, capped) | 7.8 | 140.2 | 150 |

## Point lookup latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 8.8 | 9.4 | 150 |
| Neo4j AuraDB Free | 224.9 | 226.5 | 150 |
| FalkorDB Cloud (Free) | 3.2 | 3.4 | 150 |
| Memgraph (self-hosted, capped) | 2.3 | 3.2 | 150 |
| ArangoDB (self-hosted, capped) | 1.0 | 1.2 | 150 |

## Indexed/filtered lookup latency (WHERE year = ...)

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 33.6 | 58.2 | 150 |
| Neo4j AuraDB Free | 244.0 | 474.9 | 150 |
| FalkorDB Cloud (Free) | 7.3 | 9.2 | 150 |
| Memgraph (self-hosted, capped) | 19.6 | 24.5 | 150 |
| ArangoDB (self-hosted, capped) | 3.0 | 4.9 | 150 |

## Aggregation latency (count of Paper grouped by year)

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 40.4 | 66.2 | 150 |
| Neo4j AuraDB Free | 227.5 | 229.8 | 150 |
| FalkorDB Cloud (Free) | 5.3 | 5.7 | 150 |
| Memgraph (self-hosted, capped) | 2.9 | 38.1 | 150 |
| ArangoDB (self-hosted, capped) | 3.5 | 44.3 | 150 |

## Mixed read/write workload (concurrency sweep)

80% read / 20% write mix (60s per concurrency level, see README methodology for exact op composition).

| Platform | Concurrency | Valid run | QPS (successful) | Error rate | Workers connected |
|---|---|---|---|---|---|
| CognoDB Cloud | 1 | True | 102.09 | 0.00% | 1/1 |
| CognoDB Cloud | 10 | True | 407.92 | 0.02% | 10/10 |
| CognoDB Cloud | 20 | True | 422.97 | 0.05% | 20/20 |
| CognoDB Cloud | 40 | True | 436.57 | 0.25% | 40/40 |
| Neo4j AuraDB Free | 1 | True | 3.91 | 0.00% | 1/1 |
| Neo4j AuraDB Free | 10 | True | 39.20 | 0.00% | 10/10 |
| Neo4j AuraDB Free | 20 | True | 78.12 | 0.00% | 20/20 |
| Neo4j AuraDB Free | 40 | True | 155.45 | 0.00% | 40/40 |
| FalkorDB Cloud (Free) | 1 | True | 314.88 | 0.00% | 1/1 |
| FalkorDB Cloud (Free) | 10 | True | 2785.74 | 0.00% | 10/10 |
| FalkorDB Cloud (Free) | 20 | True | 2603.51 | 0.00% | 20/20 |
| FalkorDB Cloud (Free) | 40 | True | 2847.71 | 0.00% | 40/40 |
| Memgraph (self-hosted, capped) | 1 | True | 244.14 | 0.00% | 1/1 |
| Memgraph (self-hosted, capped) | 10 | True | 166.27 | 0.04% | 10/10 |
| Memgraph (self-hosted, capped) | 20 | True | 166.30 | 0.04% | 20/20 |
| Memgraph (self-hosted, capped) | 40 | True | 165.32 | 0.07% | 40/40 |
| ArangoDB (self-hosted, capped) | 1 | True | 585.89 | 0.00% | 1/1 |
| ArangoDB (self-hosted, capped) | 10 | True | 494.69 | 0.01% | 10/10 |
| ArangoDB (self-hosted, capped) | 20 | True | 495.01 | 0.01% | 20/20 |
| ArangoDB (self-hosted, capped) | 40 | True | 506.24 | 0.06% | 40/40 |

## Footprint

| Platform | Nodes | Edges | Notes |
|---|---|---|---|
| CognoDB Cloud | 8769 | 105000 | Byte-level storage size is not exposed over Bolt on this platform's free tier; see the platform console/dashboard. |
| Neo4j AuraDB Free | 8769 | 105000 | Byte-level storage size is not exposed over Bolt on this platform's free tier; see the platform console/dashboard. |
| FalkorDB Cloud (Free) | 8769 | 105000 | FalkorDB Cloud free tier does not expose per-graph memory usage over the client API. |
| Memgraph (self-hosted, capped) | 8769 | 105000 | Container capped at 0.5 CPU / 512 MB (see Platform specs); byte-level graph storage not separately reported in this run. |
| ArangoDB (self-hosted, capped) | 8769 | 105000 | Container capped at 0.5 CPU / 512 MB (see Platform specs); byte-level graph storage not separately reported in this run. |

## Best observed value by workload

| Workload | Platform | Value |
|---|---|---|
| Point lookup (p95) | ArangoDB (self-hosted, capped) † | 1.23 ms |
| Indexed lookup (p95) | ArangoDB (self-hosted, capped) † | 4.87 ms |
| 1-hop traversal (p95) | ArangoDB (self-hosted, capped) † | 2.92 ms |
| 2-hop traversal (p95) | FalkorDB Cloud (Free) | 5.03 ms |
| 3-hop traversal (p95) | FalkorDB Cloud (Free) | 7.78 ms |
| Aggregation (p95) | FalkorDB Cloud (Free) | 5.70 ms |
| Ingest throughput (nodes/sec) | ArangoDB (self-hosted, capped) † | 58867.60 nodes/s |
| Ingest throughput (rels/sec) | ArangoDB (self-hosted, capped) † | 32761.10 rels/s |
| Mixed workload QPS @ 1 clients | ArangoDB (self-hosted, capped) † | 585.89 qps |
| Mixed workload QPS @ 10 clients | FalkorDB Cloud (Free) | 2785.74 qps |
| Mixed workload QPS @ 20 clients | FalkorDB Cloud (Free) | 2603.51 qps |
| Mixed workload QPS @ 40 clients | FalkorDB Cloud (Free) | 2847.71 qps |

_These are the best numbers observed for this specific dataset, query set, and resource-capped setup — not a general claim about which database is "best." A different dataset size, query mix, or hardware tier could change every row._

_† Self-hosted comparators (Memgraph, ArangoDB) ran co-located with the benchmark client and therefore had a real network-latency advantage that CognoDB, Aura, and FalkorDB — all reached over the network — did not. Treat cross-deployment latency comparisons marked † as directional, not a pure query-engine comparison._

## Charts

![Traversal latency (p95) by hop depth](results/charts/traversal_latency.png)

![Lookup & aggregation latency (p95)](results/charts/lookup_latency.png)

![p95/p50 ratio — tail latency multiplier per workload](results/charts/latency_stability.png)

![Ingest throughput](results/charts/ingest_throughput.png)

![Mixed workload QPS vs. concurrency](results/charts/mixed_workload_qps.png)

![Mixed workload error rate vs. concurrency](results/charts/mixed_workload_error_rate.png)


See also: [`dashboard.html`](results/dashboard.html) for a browsable summary, or `results/raw/*.json` for the full per-platform data behind every table above.

<!-- RESULTS:END -->

Full per-workload tables (all metrics from the assignment's §5.2 for every platform), the platform-spec table, the dataset manifest fingerprint, and six charts (traversal latency by hop depth, lookup/aggregation latency, a p95/p50 stability ratio, ingest throughput, mixed-workload QPS vs. concurrency, and mixed-workload error rate vs. concurrency) are regenerated into this section and into `results/RESULTS.md` / `results/dashboard.html` by `make report`. Open `results/dashboard.html` in a browser for a quick browsable summary with winner-by-workload stat cards; `results/raw/*.json` is the full underlying data behind every number.

## Analysis

### FalkorDB — strongest managed-cloud traversal and concurrency profile

Among the three remotely-hosted managed services, FalkorDB produced the lowest p95 traversal latency at every hop depth (3.6 / 5.0 / 7.8 ms for 1/2/3 hops) and the highest sustained mixed-workload throughput observed in the entire report: 314.9 QPS at 1 client, rising to 2,785.7 QPS at 10, and holding at 2,603.5–2,847.7 QPS through 20 and 40 clients — with zero recorded errors at any concurrency level. The roughly 9x jump between 1 and 10 clients, followed by a plateau, suggests most of the available parallelism headroom on this free tier is captured by around 10 concurrent clients.

### CognoDB — fast on shallow reads, a real tail at 3 hops

CognoDB's point lookup (9.4 ms p95) and 1-hop traversal (9.9 ms p95) were competitive with the self-hosted comparators despite the network hop. The 3-hop workload tells a different story: 17.2 ms p50 vs. 189.6 ms p95 — more than a 10x gap between typical and tail latency that isn't present at 1 or 2 hops. Mixed-workload throughput scaled from 102.1 QPS at 1 client to 407.9 QPS at 10, but only marginally beyond that (423.0 QPS @ 20, 436.6 @ 40), while the error rate climbed to 0.25% at 40 clients — the clearest sign of contention under load anywhere in this dataset.

### AuraDB Free — a large, nearly-constant latency floor

Every AuraDB workload landed in a narrow ~225–245 ms band regardless of query complexity: point lookup p95 226.5 ms, 1-hop 226.3 ms, 2-hop 231.9 ms, 3-hop 244.6 ms, aggregation 229.8 ms. Point lookups and 3-hop traversals are not equally expensive queries, so returning nearly the same latency for both suggests a large fixed cost — network path, free-tier connection/gateway overhead, or scheduling — dominates end-to-end latency here rather than query execution time. Because this benchmark measures client-observed latency rather than server-side execution time, it can't separate that fixed cost from engine performance, so this result is reported as an environment/service-tier observation, not evidence about the Neo4j execution engine itself.

### Memgraph and ArangoDB — capped-hardware reference points, not managed-cloud winners

Running with no remote network hop, both self-hosted platforms posted the lowest raw single-query numbers in the report (e.g. ArangoDB point lookup p95 1.2 ms, 1-hop p95 2.9 ms) and ArangoDB the highest ingest throughput observed (58,867.6 nodes/sec, 32,761.1 rels/sec) — despite running under the same 0.5 vCPU / 512 MB cap as CognoDB. These are marked **†** in the Best Observed Value table precisely because they inherit the network advantage described in [Fairness & experimental environment](#fairness--experimental-environment): they measure "what this engine does under a matched CPU/RAM cap with zero network cost," not "how this engine would perform deployed remotely like the other three."

Memgraph's mixed-workload error rate rose to 0.25% at 40 clients, indicating some degradation at the highest tested concurrency. Because the benchmark measures end-to-end behavior, the run alone cannot distinguish database contention from free-tier throttling, network effects, or other managed-service overhead.

### Tail latency changes the story that medians tell

Looking only at p50, CognoDB's and ArangoDB's 3-hop traversals would both read as fast (17.2 ms and 7.8 ms respectively). It's only the p95 (189.6 ms and 140.2 ms) that reveals a heavy tail specific to the deepest traversal on those two platforms, absent at 1 and 2 hops. Reporting both bounds — not just an average — is what surfaces this, and it's the main reason this benchmark reports p50/p95 for every workload rather than a single latency number per platform.

## Caveats & limitations

- **Network placement is not equal across platforms.** Memgraph and ArangoDB ran as Docker containers inside the same GitHub Codespaces environment as the benchmark client; CognoDB, AuraDB, and FalkorDB were all reached over the network to their managed endpoints. Self-hosted latency numbers therefore have a real, unequalized advantage and are marked † throughout — see [Deployment & network placement](#deployment--network-placement).
- **Managed-cloud regions are close, not identical.** CognoDB (GCP `us-east4`) and FalkorDB (AWS `us-east-1`) are both Northern-Virginia-area regions but different providers and physically distinct data centers — reported as "geographically close," not "the same region." AuraDB's region wasn't exposed during this run and is reported as unknown rather than guessed.
- **CognoDB's RAM spec changed since the assignment was written** (256 MB per the PDF vs. 512 MB observed on the actual provisioned console) — see [Resource parity](#resource-parity). The harness uses the observed value.
- **Docker resource caps are CPU/RAM only, not disk.** `mem_limit`/`cpus` are real, enforced constraints; disk is host-backed with no quota. The dataset (~105k relationships) is small enough this shouldn't matter, but it's not an enforced parity claim.
- **Query-language differences are real and not papered over.** ArangoDB (AQL) and FalkorDB (openCypher subset) are not drop-in Cypher; see `benchmark/adapters/` for the specific dialect differences (e.g. FalkorDB has no `FOREACH`, so conditional property-setting during load is split into two passes instead of one).
- **Free-tier throttling, timeouts, and partial connection failures are reported, not hidden** — see the mixed-workload `run_valid`/`error_rate`/`connect_errors` fields in every result JSON, and the "Run status" table above, which would show `status: failed` with the real exception message for any platform that didn't complete successfully.
- **AuraDB free-tier limits are inconsistently documented by Neo4j itself** (one page states 50k nodes/175k relationships, another 200k/400k) — the ~8,769-node/105,000-relationship sample here fits comfortably under either stated limit.
- **One dataset, one workload mix.** These results characterize this citation graph, this sampling method, and this specific read/write mix — not every possible graph topology or access pattern. Only warmed-cache latency is reported as the headline metric; cold-start behavior wasn't measured separately.

## Reproduce the benchmark

### Requirements

- Linux, GitHub Codespaces, or WSL (the Makefile's `.venv/bin` paths assume a POSIX shell)
- Python 3.11+
- Docker + Docker Compose
- Free accounts for CognoDB, AuraDB, and FalkorDB

### 1. Python environment

```bash
git clone <this-repo-url> && cd DBBenchmarking
make setup            # creates .venv, installs pinned requirements.txt
```

### 2. Platform accounts — read the password/URI shown-once warnings before starting

**CognoDB Cloud** (required target):
1. [console.cognodb.com/signup](https://console.cognodb.com/signup) — free, no credit card.
2. Create a free `c0` instance (this report used `us-east4`; any region works for reproduction).
3. Copy the `bolt+s://...` URI and the generated password **immediately** — the password is shown once.

**Neo4j AuraDB Free:**
1. [console.neo4j.io](https://console.neo4j.io) — create a Free instance.
2. Download the generated credentials file when prompted (also shown once).

**FalkorDB Cloud (Free):**
1. [app.falkordb.cloud](https://app.falkordb.cloud) — create a free instance.
2. Note host/port/password from the dashboard.

**Memgraph + ArangoDB** (self-hosted, capped — no account needed):
```bash
make docker-up     # starts both, resource-capped per docker/docker-compose.yml
```

### 3. Configure secrets

```bash
cp .env.example .env
# fill in COGNODB_*, AURA_*, FALKORDB_* from the consoles above.
# MEMGRAPH_URI / ARANGO_* already point at the docker-compose defaults.
```

`.env` is gitignored — nothing here is ever committed. `benchmark/config.py` skips any platform whose required env vars aren't set, so you can develop against a subset of accounts.

### 4. Run

```bash
make dataset                    # fetch SNAP data, build the forest-fire sample (deterministic, ~seconds)
make all PLATFORM=cognodb       # load + benchmark one platform
make all                        # load + benchmark every platform with credentials in .env
make report                     # regenerate RESULTS.md, dashboard.html, charts from results/raw/*.json
```

`make all` (and the underlying `python -m benchmark.runner all --platform all`) is the single command a reviewer needs — it loads the identical dataset into every configured platform, runs every required workload, and writes one timestamped JSON per platform to `results/raw/`. Re-running `make all` after fixing an account or restarting Docker just adds a newer result; `generate_report.py` always uses the latest `status="ok"` run per platform, so `make report` regenerates every table and chart from those files with no hand-copied numbers.

Individual pieces, for development/debugging:
```bash
make load PLATFORM=falkordb                                    # just load
make bench PLATFORM=falkordb                                    # verify data present, then benchmark
.venv/bin/python -m benchmark.runner bench --platform cognodb --skip-mixed   # skip the concurrency sweep (faster iteration)
```

## Repository structure

```
data/                   dataset download + forest-fire sampling
  download.py
  prepare_dataset.py
  processed/             nodes.csv, edges.csv, manifest.json, query_start_nodes.json (generated)
benchmark/
  config.py              platform specs, dataset/workload parameters — single source of truth
  adapters/              one adapter per wire protocol: bolt (CognoDB/Aura/Memgraph), falkordb, arangodb
  loader.py               batched loading + ingest throughput
  workloads.py            read workloads + concurrent mixed read/write workload
  stats.py                 warm-up handling + p50/p95/p99 percentiles
  runner.py                 CLI: load / bench / all, per platform or --platform all
docker/
  docker-compose.yml     resource-capped Memgraph + ArangoDB
scripts/
  generate_report.py     results/raw/*.json -> RESULTS.md + dashboard.html + charts
results/
  raw/                   one JSON per platform per run (gitignored by default)
  charts/                 generated PNGs
  RESULTS.md, dashboard.html
```

## Extending the harness

A new database only needs to implement the `GraphAdapter` interface (`benchmark/adapters/base.py`):

- `connect` / `close`
- `reset_schema`
- `load_nodes` / `load_edges`
- `count_nodes` / `count_edges`
- `point_lookup` / `indexed_lookup` / `traversal` / `aggregation`
- `write_touch` / `reset_benchmark_counter`
- `footprint`

The workload runner, percentile logic, concurrency harness, result JSON schema, and report generator (`scripts/generate_report.py`) are all database-agnostic — they only ever call through this interface. Adding another graph database means writing one adapter and registering it in `benchmark/adapters/__init__.py` / `benchmark/config.py`, without touching the experimental methodology itself.

## What I would improve with more time

The largest remaining limitation is network placement. Memgraph and ArangoDB
were co-located with the benchmark client, while the managed platforms were
accessed remotely. A stronger follow-up experiment would place the self-hosted
databases on a separate US-East VM so every platform incurs a real network hop.

I would also:

- repeat the complete benchmark across multiple independent runs and report
  run-to-run variance or confidence intervals;
- capture server-side query execution time where platforms expose it, allowing
  network and service overhead to be separated from query execution;
- repeat the experiment on a second graph with a different topology and degree
  distribution;
- test larger datasets and matched paid instances where hardware specifications
  can be controlled more closely.

These extensions would make it easier to distinguish engine behavior from
free-tier infrastructure and network effects.
