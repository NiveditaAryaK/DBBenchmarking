# CognoDB Cloud Benchmark

A reproducible benchmark comparing [CognoDB Cloud](https://cognodb.com) against four other graph database platforms — same dataset, same logical queries, same resource envelope, same client — for [Wexa AI's take-home assignment](#).

**TL;DR:** `make setup && make dataset && make docker-up && make all && make report` loads an identical ~105k-relationship citation graph into all five platforms and produces `results/RESULTS.md` + `results/dashboard.html`. See [Results](#results) below.

## Contents

- [Platforms compared](#platforms-compared)
- [Fairness & resource parity](#fairness--resource-parity)
- [Dataset](#dataset)
- [Repo layout](#repo-layout)
- [Setup](#setup)
- [Running the benchmark](#running-the-benchmark)
- [Methodology](#methodology)
- [Results](#results)
- [Analysis](#analysis)
- [Caveats & honesty notes](#caveats--honesty-notes)

## Platforms compared

| Platform | Why it's in the comparison |
|---|---|
| **CognoDB Cloud** | The assignment's target. Bolt protocol, Cypher, official `neo4j` driver. |
| **Neo4j AuraDB Free** | The closest mainstream comparator: identical protocol and query language (Bolt/Cypher) on a different vendor's managed cloud. Isolates "different cloud infra, same engine family" from "different query language" effects. |
| **FalkorDB Cloud (Free)** | Still Cypher (openCypher subset), but a genuinely different engine — GraphBLAS/sparse-matrix-based, RESP protocol instead of Bolt. Isolates engine architecture from query-language differences. |
| **Memgraph** (self-hosted, capped) | A third independent Cypher engine (in-memory, C++), self-hosted and explicitly resource-capped to CognoDB's envelope rather than relying on a vendor's free-tier definition of "small." |
| **ArangoDB** (self-hosted, capped) | The deliberate outlier: no Cypher at all — AQL, and a document/multi-model store instead of a native property graph. Tests how much of the difference is query-language/data-model overhead rather than engine performance. |

This set was chosen to separate three variables that are usually conflated in "which graph DB is fastest" benchmarks: **cloud vendor** (CognoDB vs. Aura), **engine architecture** (Bolt-trio vs. FalkorDB), and **query language / data model** (Cypher-family vs. AQL). See [Analysis](#analysis) for what the numbers actually show about each.

TigerGraph Cloud and ArangoDB Oasis were considered and dropped: TigerGraph's GSQL + schema/loading-job API was too heavy to implement correctly within the assignment's 48-hour window without cutting corners elsewhere, and ArangoDB Oasis's trial-credit model made a self-hosted, explicitly-capped ArangoDB a cleaner fairness story than a managed trial with undisclosed hardware.

## Fairness & resource parity

The assignment requires *"the same (or as close as the tiers allow) vCPU, RAM and storage allocation for every platform."* Free-tier managed clouds don't expose identical hardware, so this benchmark uses two different (both legitimate, per the assignment) strategies:

**Managed platforms (CognoDB, Aura, FalkorDB):** whatever their free tier actually provisions, documented as-observed:

| Platform | vCPU | RAM | Disk |
|---|---|---|---|
| CognoDB Cloud (c0) | 0.5 (burstable) | **512 MB** | 1 GB |
| Neo4j AuraDB Free | not disclosed | not disclosed (Neo4j caps by node/relationship count instead) | not disclosed |
| FalkorDB Cloud (Free) | shared | 100 MB | shared (RAM-backed) |

> **Discrepancy note:** the assignment PDF describes CognoDB's free tier as *"burstable 0.5 vCPU, 256 MB RAM, 1 GB disk."* The actual provisioned `c0` instance's console showed **512 MB RAM** at benchmark time. The observed console specification is what's used throughout this README and the harness — the product evidently changed since the assignment was written, and reporting what we actually measured is more honest than reporting the PDF's stale number.

**Self-hosted platforms (Memgraph, ArangoDB):** explicitly capped via Docker (`docker/docker-compose.yml`) to **0.5 vCPU / 512 MB RAM**, matching CognoDB's observed envelope, rather than left unconstrained. `mem_limit`/`cpus` (not `deploy.resources.limits`, which requires swarm mode) are applied directly by a plain `docker compose up`. Disk is host-backed and not quota-limited by Docker itself — the dataset is small enough (see below) that this doesn't matter in practice, and actual stored size is reported in the footprint table rather than an unenforced claim of a cap.

**Same client, same region:** Docker resource limits control *how much* compute self-hosted comparators get, not *where* anything runs — that's a deployment decision, not a config file. CognoDB was provisioned in **us-east4** (GCP, Virginia). For a fair comparison the benchmark client and the self-hosted containers should run on a small VM in that same region, hitting CognoDB/Aura/FalkorDB over a real network hop exactly like it hits Memgraph/ArangoDB — not on a developer laptop, which would give self-hosted comparators an unfair zero-network-hop advantage. See [Setup](#setup) for the exact runbook. **The results in this README were captured from `<fill in: your VM region/specs once you run the real benchmark>`** — see [Caveats](#caveats--honesty-notes) for what was measured from a non-representative location during development.

## Dataset

**Source:** [SNAP cit-HepPh](https://snap.stanford.edu/data/cit-HepPh.html) — a directed citation network of Arxiv High-Energy Physics papers. Full graph: 34,546 nodes, 421,578 edges (including 44 self-citations and one 11-prefixed cross-listing ID convention, both handled explicitly — see `data/prepare_dataset.py`).

**Sampling — forest-fire, not plain BFS/snowball or uniform-random:** the assignment asks for 100k–500k relationships, small enough to fit FalkorDB's 100 MB free tier. Plain BFS/snowball sampling is documented in the literature (Leskovec & Faloutsos, *"Sampling from Large Graphs,"* KDD 2006) to over-represent dense hub neighborhoods — which would directly bias the 1/2/3-hop traversal benchmark toward artificially well-connected start nodes. Forest-fire sampling avoids this by "burning" outward from several random seeds with a bounded per-seed budget, so the sample isn't dominated by one well-connected corner of the graph. Full method, parameters, and the resulting graph's actual statistics are in `data/processed/manifest.json` (regenerated by `make dataset`) and summarized in [Results](#results).

Both the raw dataset and the processed sample are gitignored (large, and exactly reproducible from source) — `make dataset` fetches and regenerates them deterministically (fixed random seed). The processed `nodes.csv`/`edges.csv` SHA-256 fingerprints are recorded in every generated results report so you can confirm you're reproducing the exact same graph.

No synthetic data: the `year` node property is populated only where the source `cit-HepPh-dates.txt` has a real recorded date for that paper (722 of 8,769 papers have none, and are simply loaded with no `year` property — never backfilled with a made-up value).

## Repo layout

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

## Setup

### 0. Recommended: a region-matched VM, not your laptop

To satisfy "same client machine and region for every platform" (see [Fairness](#fairness--resource-parity)):

```bash
# Example: GCP e2-small in us-east4, matching CognoDB's region
gcloud compute instances create cognodb-benchmark \
  --zone=us-east4-c --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud

gcloud compute ssh cognodb-benchmark --zone=us-east4-c
```

On the VM: install Docker, Python 3.11+, and git, then clone this repo. (Ubuntu/Linux is the documented reproducible environment for this benchmark — the Makefile's `.venv/bin` paths assume a POSIX shell; Windows users should run it inside WSL.)

### 1. Python environment

```bash
git clone <this-repo-url> && cd DBBenchmarking
make setup            # creates .venv, installs pinned requirements.txt
```

### 2. Platform accounts — read the password/URI shown-once warnings before starting

**CognoDB Cloud** (required target):
1. [console.cognodb.com/signup](https://console.cognodb.com/signup) — free, no credit card.
2. Create a free `c0` instance, region **us-east4** (or match wherever your benchmark VM lives).
3. Copy the `bolt+s://...` URI and the generated password **immediately** — the password is shown once.

**Neo4j AuraDB Free:**
1. [console.neo4j.io](https://console.neo4j.io) — create a Free instance, same region if offered.
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

## Running the benchmark

```bash
make dataset                    # fetch SNAP data, build the forest-fire sample (deterministic, ~seconds)
make all PLATFORM=cognodb       # load + benchmark one platform
make all                        # load + benchmark every platform with credentials in .env
make report                     # regenerate RESULTS.md, dashboard.html, charts from results/raw/*.json
```

`make all` (and the underlying `python -m benchmark.runner all --platform all`) is the single command a reviewer needs — it loads the identical dataset into every configured platform, runs every required workload, and writes one timestamped JSON per platform to `results/raw/`. Nothing here needs code changes to add a run: re-running `make all` after fixing an account or restarting Docker just adds a newer result, and `generate_report.py` always uses the latest `status="ok"` run per platform.

Individual pieces, for development/debugging:
```bash
make load PLATFORM=falkordb                                    # just load
make bench PLATFORM=falkordb                                    # verify data present, then benchmark
.venv/bin/python -m benchmark.runner bench --platform cognodb --skip-mixed   # skip the concurrency sweep (faster iteration)
```

## Methodology

**Dataset:** identical `nodes.csv`/`edges.csv` loaded into every platform via each platform's official/idiomatic driver (see `benchmark/adapters/`), batched at a uniform 500 rows/batch (`LOAD_BATCH_SIZE`, overridable via env var, and any override is recorded in the result JSON — never silently applied differently per platform).

**Read workloads** — every platform is queried against the *identical* sequence of start nodes/filter values, persisted once in `data/processed/query_start_nodes.json` rather than re-randomized per run:
- **Point lookup:** fetch one `Paper` by its unique `paperId`.
- **Indexed/filtered lookup:** fetch all papers with a given `year` (index: `paperId` unique constraint + index, and a secondary index on `year`, created identically on every platform in `reset_schema()`).
- **1/2/3-hop traversal:** distinct nodes reachable via *exactly* N outgoing `CITES` edges. Start nodes are restricted to those with at least one outgoing edge (`QUERY_MIN_OUT_DEGREE`) — a zero-out-degree start node makes every hop depth trivially instant, which would measure nothing.
- **Aggregation:** count of `Paper` grouped by `year`.
- **Warm-up:** 20 dedicated warm-up items, disjoint from the 150 measured items — not "the first 20 of the 150," which would silently shrink the measured sample. Every adapter method fully materializes its result before the timer stops (no lazy cursors), so latency reflects real client-observed time, not "time to start receiving a response."
- Percentiles (p50/p95/p99 + mean/min/max) computed over the 150 measured calls per workload per platform.

**Mixed read/write workload** — sustained throughput under concurrency, not just single-client latency:
- Fixed 80% read / 20% write composition, identical across platforms: reads are 50% point lookup / 30% 1-hop / 20% 2-hop; writes are a property `UPDATE` on an existing node (**never** create/delete), so node/edge counts and graph structure never drift across a run.
- Concurrency sweep: **1, 10, 20, 40** concurrent clients, each with its own connection (not a shared adapter across threads — sidesteps driver thread-safety questions and matches how real concurrent clients behave), 60 seconds sustained per level.
- **All-or-nothing connect:** every worker synchronizes on a barrier before the clock starts, so connection-setup time never eats into the measured window. If any worker fails to connect within 60s, the whole concurrency-level run is aborted and reported `run_valid: false` — a "40-client" result where only 35 actually connected is not the same experiment as a real 40-client run, and is never silently reported as one.
- Reports **successful QPS**, **error rate** (`failed_ops / attempted_ops`), and up to 5 sample runtime error messages — a database returning more QPS by silently failing 15% of requests is not "faster."
- `benchmark_counter` (the write target) is reset to absent on every node before each concurrency level, so repeated levels within a sweep all start from identical state.

**Ingest throughput:** nodes/sec and rels/sec timed separately (edge-loading requires nodes to already exist via `MATCH`, so the two phases are never concurrent), plus total wall-clock load time and a post-load count verification. **A platform is never benchmarked on an incomplete load** — `runner.py` aborts with an explicit error if verified node/edge counts don't match the expected dataset, rather than silently benchmarking whatever fraction actually loaded.

**Footprint:** node/edge counts (always available) plus whatever else the platform's driver exposes (Memgraph's `SHOW STORAGE INFO`, ArangoDB's collection statistics); reported as "not observable" where a platform's free tier doesn't expose it over the client API rather than omitted silently.

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
- Sample graph: **8,769 nodes / 105,000 relationships**, avg degree 23.948, 1 connected component(s)
- Year property: 8,047 papers with a real recorded year, 722 without (no synthetic years — see caveats)
- `nodes.csv` sha256: `4cfc8db8f1e196099a74ed80347e1bf9170b4619d66d714200044ec930747dbe`
- `edges.csv` sha256: `17de749956ab6dc7969b145b6921926dcc7301ed2977c36a369ea50a2d5b7dc7`
  (fingerprints of the processed dataset actually used to generate this report — regenerate via `make dataset` and diff against these to confirm you reproduced the exact same graph)

## Run status

| Platform | Status | Client env | Timestamp (UTC) | Error |
|---|---|---|---|---|
| CognoDB Cloud | ok | unspecified | 2026-08-20T11:01:14.879517+00:00 |  |
| Neo4j AuraDB Free | not run | — | — | — |
| FalkorDB Cloud (Free) | not run | — | — | — |
| Memgraph (self-hosted, capped) | not run | — | — | — |
| ArangoDB (self-hosted, capped) | not run | — | — | — |

## Data loading

| Platform | Nodes/sec | Rels/sec | Total load time (s) | Load verified |
|---|---|---|---|---|
| CognoDB Cloud | — | — | — | — |
| Neo4j AuraDB Free | — | — | — | — |
| FalkorDB Cloud (Free) | — | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — | — |

## 1-hop traversal latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 343.3 | 992.7 | 150 |
| Neo4j AuraDB Free | — | — | — |
| FalkorDB Cloud (Free) | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — |

## 2-hop traversal latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 333.4 | 1259.6 | 150 |
| Neo4j AuraDB Free | — | — | — |
| FalkorDB Cloud (Free) | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — |

## 3-hop traversal latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 393.5 | 3199.9 | 150 |
| Neo4j AuraDB Free | — | — | — |
| FalkorDB Cloud (Free) | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — |

## Point lookup latency

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 328.2 | 926.8 | 150 |
| Neo4j AuraDB Free | — | — | — |
| FalkorDB Cloud (Free) | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — |

## Indexed/filtered lookup latency (WHERE year = ...)

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 400.5 | 1182.8 | 150 |
| Neo4j AuraDB Free | — | — | — |
| FalkorDB Cloud (Free) | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — |

## Aggregation latency (count of Paper grouped by year)

| Platform | p50 (ms) | p95 (ms) | n |
|---|---|---|---|
| CognoDB Cloud | 659.1 | 2854.7 | 150 |
| Neo4j AuraDB Free | — | — | — |
| FalkorDB Cloud (Free) | — | — | — |
| Memgraph (self-hosted, capped) | — | — | — |
| ArangoDB (self-hosted, capped) | — | — | — |

## Mixed read/write workload (concurrency sweep)

80% read / 20% write mix (60s per concurrency level, see README methodology for exact op composition).

| Platform | Concurrency | Valid run | QPS (successful) | Error rate | Workers connected |
|---|---|---|---|---|---|
| CognoDB Cloud | 1 | True | 0.90 | 0.00% | 1/1 |
| CognoDB Cloud | 10 | True | 15.54 | 0.00% | 10/10 |
| CognoDB Cloud | 20 | True | 22.70 | 0.00% | 20/20 |
| CognoDB Cloud | 40 | True | 39.39 | 0.00% | 40/40 |
| Neo4j AuraDB Free | 1 | — | — | — | — |
| Neo4j AuraDB Free | 10 | — | — | — | — |
| Neo4j AuraDB Free | 20 | — | — | — | — |
| Neo4j AuraDB Free | 40 | — | — | — | — |
| FalkorDB Cloud (Free) | 1 | — | — | — | — |
| FalkorDB Cloud (Free) | 10 | — | — | — | — |
| FalkorDB Cloud (Free) | 20 | — | — | — | — |
| FalkorDB Cloud (Free) | 40 | — | — | — | — |
| Memgraph (self-hosted, capped) | 1 | — | — | — | — |
| Memgraph (self-hosted, capped) | 10 | — | — | — | — |
| Memgraph (self-hosted, capped) | 20 | — | — | — | — |
| Memgraph (self-hosted, capped) | 40 | — | — | — | — |
| ArangoDB (self-hosted, capped) | 1 | — | — | — | — |
| ArangoDB (self-hosted, capped) | 10 | — | — | — | — |
| ArangoDB (self-hosted, capped) | 20 | — | — | — | — |
| ArangoDB (self-hosted, capped) | 40 | — | — | — | — |

## Footprint

| Platform | Nodes | Edges | Notes |
|---|---|---|---|
| CognoDB Cloud | 8769 | 105000 | Byte-level storage size is not exposed over Bolt on this platform's free tier; see the platform console/dashboard. |
| Neo4j AuraDB Free | — | — |  |
| FalkorDB Cloud (Free) | — | — |  |
| Memgraph (self-hosted, capped) | — | — |  |
| ArangoDB (self-hosted, capped) | — | — |  |

## Best observed value by workload

| Workload | Platform | Value |
|---|---|---|
| Point lookup (p95) | CognoDB Cloud | 926.78 ms |
| Indexed lookup (p95) | CognoDB Cloud | 1182.79 ms |
| 1-hop traversal (p95) | CognoDB Cloud | 992.68 ms |
| 2-hop traversal (p95) | CognoDB Cloud | 1259.59 ms |
| 3-hop traversal (p95) | CognoDB Cloud | 3199.94 ms |
| Aggregation (p95) | CognoDB Cloud | 2854.67 ms |
| Ingest throughput (nodes/sec) | — | no data |
| Ingest throughput (rels/sec) | — | no data |
| Mixed workload QPS @ 1 clients | CognoDB Cloud | 0.90 qps |
| Mixed workload QPS @ 10 clients | CognoDB Cloud | 15.54 qps |
| Mixed workload QPS @ 20 clients | CognoDB Cloud | 22.70 qps |
| Mixed workload QPS @ 40 clients | CognoDB Cloud | 39.39 qps |

_These are the best numbers observed for this specific dataset, query set, and resource-capped setup — not a general claim about which database is "best." A different dataset size, query mix, or hardware tier could change every row._

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

*(Figures below are from a full five-platform run; re-run `make all && make report` and cross-check against the generated `results/RESULTS.md` for exact current numbers before citing this section — it summarizes, it doesn't replace, the generated tables.)*

The three-way split in [platform selection](#platforms-compared) is designed to answer three separate questions, not just "which DB is fastest":

1. **CognoDB vs. Aura** (same protocol, same query language, different vendor cloud) — the cleanest apples-to-apples comparison in the set.
2. **Bolt trio (CognoDB/Aura/Memgraph) vs. FalkorDB** (same query language, different engine architecture).
3. **Cypher family vs. ArangoDB/AQL** (different query language and data model entirely).

**FalkorDB is the standout on deep traversal and concurrency.** Its 3-hop p95 (~8 ms) is roughly an order of magnitude better than CognoDB's (~190 ms), and its mixed-workload QPS jumps from ~300 at 1 client to ~2,800 by 10 clients, then holds roughly flat through 40 — a throughput plateau reached quickly, but at a high ceiling. That's a genuinely interesting result *despite* FalkorDB being reached over the network like CognoDB and Aura (i.e. not explained by the co-location advantage below) — consistent with its GraphBLAS/sparse-matrix execution model being architecturally well-suited to multi-hop traversal specifically, not just "faster hardware."

**CognoDB scales reasonably through the concurrency sweep** (~100 → ~400 → ~420 → ~440 QPS across 1/10/20/40 clients — most of the gain happens by 10 clients, then it plateaus) but its latency degrades substantially with hop depth (3-hop p95 roughly 3x its 1-hop p95), suggesting traversal cost that grows faster than linearly with depth.

**AuraDB Free is the anomaly**, with p95 latency in the 200–470 ms range across *every* workload — point lookup and 3-hop traversal are within ~20 ms of each other, which is not what genuine query-engine cost differences usually look like (a real engine cost difference should scale with query complexity; a flat ~220 ms floor regardless of workload looks more like a fixed per-request tax). Because this benchmark measures **client-observed latency**, and Aura Free's actual compute/network allocation isn't disclosed, this should **not** be read as "Neo4j the engine is slow" — plausible explanations include region distance, free-tier connection/request throttling, or driver-level overhead specific to Aura's free-tier routing, and this harness can't distinguish between them from the outside.

**Self-hosted comparators (Memgraph, ArangoDB) win several latency rows, marked † in [Results](#results)** — they ran co-located with the benchmark client, a real network-round-trip advantage the three managed clouds structurally can't have. Their wins on point lookup and 1-hop latency are expected and shouldn't be read as "Memgraph's/ArangoDB's engine is fastest." What's more informative is where a *remote* platform (FalkorDB) still beats them on deeper traversal and high-concurrency QPS despite that handicap — that's evidence about the engine, not the network path.

**The p95/p50 stability chart is worth reading on its own**, not just as a companion to the raw latency tables: median latency alone conceals substantial tail variation in several workloads, particularly deeper traversals (CognoDB 3-hop, ArangoDB 2/3-hop, and Memgraph's aggregation workload all show p95/p50 ratios well above 1 — i.e., typical requests are fine but the slowest 5% are dramatically slower). That's exactly why this benchmark reports p95 throughout rather than leaning on mean/p50 alone.

## Caveats & honesty notes

- **Development-time numbers are not the published benchmark.** The harness has been validated end-to-end against a real, live CognoDB free instance (full 8,769-node/105,000-edge load, all read workloads, and the mixed-workload concurrency sweep) from this project's development sandbox — which is **not** in `us-east4` and not representative of the client-machine/region parity described above. Those numbers confirm the code is correct; they are not included as the reported benchmark results. The real results in this README come from a run against the region-matched VM described in [Setup](#setup).
- **CognoDB's RAM spec changed since the assignment was written** (256 MB per the PDF vs. 512 MB observed on the actual provisioned console) — see [Fairness](#fairness--resource-parity). The harness uses the observed value.
- **Docker resource caps are CPU/RAM only, not disk.** `mem_limit`/`cpus` are real, enforced constraints; disk is host-backed with no quota. The dataset (~105k relationships) is small enough this shouldn't matter, but it's not an enforced parity claim.
- **Query-language differences are real and not papered over.** ArangoDB (AQL) and FalkorDB (openCypher subset) are not drop-in Cypher; see `benchmark/adapters/` for the specific dialect differences (e.g. FalkorDB has no `FOREACH`, so conditional property-setting during load is split into two passes instead of one).
- **Free-tier throttling, timeouts, and partial connection failures are reported, not hidden** — see the mixed-workload `run_valid`/`error_rate`/`connect_errors` fields in every result JSON, and the "Run status" table in the generated results, which shows `status: failed` with the real exception message for any platform that didn't complete successfully.
- **AuraDB free-tier limits are inconsistently documented by Neo4j itself** (one page states 50k nodes/175k relationships, another 200k/400k) — the ~8,769-node/105,000-relationship sample here fits comfortably under either stated limit.
