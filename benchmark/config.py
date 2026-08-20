"""Central config: platform specs, dataset targets, workload parameters.

All secrets come from environment variables (see .env.example). Nothing here
is a credential — this file is safe to commit and read in a PR review.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
RESULTS_RAW = ROOT / "results" / "raw"
RESULTS_CHARTS = ROOT / "results" / "charts"

for d in (DATA_RAW, DATA_PROCESSED, RESULTS_RAW, RESULTS_CHARTS):
    d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Dataset: SNAP cit-HepPh, forest-fire sampled down to fit the smallest free
# tier. See data/prepare_dataset.py for why forest-fire (not plain BFS/
# snowball, and not uniform-random) was chosen.
# --------------------------------------------------------------------------
DATASET_EDGES_URL = "https://snap.stanford.edu/data/cit-HepPh.txt.gz"
DATASET_DATES_URL = "https://snap.stanford.edu/data/cit-HepPh-dates.txt.gz"
DATASET_RAW_EDGES_FILE = DATA_RAW / "cit-HepPh.txt"
DATASET_RAW_DATES_FILE = DATA_RAW / "cit-HepPh-dates.txt"
DATASET_NODES_CSV = DATA_PROCESSED / "nodes.csv"
DATASET_EDGES_CSV = DATA_PROCESSED / "edges.csv"
DATASET_MANIFEST = DATA_PROCESSED / "manifest.json"

# The assignment requires >= 100,000 relationships. We target just above that
# floor (105k), not the top of the suggested 100k-500k range, because the
# tightest free tier we're comparing against is FalkorDB Cloud's 100MB RAM
# instance — 120k+ edges gave no extra benchmark credibility but higher risk
# of OOM once indexes, edge metadata and query overhead are counted.
SAMPLE_TARGET_EDGES = 105_000
SAMPLE_MAX_NODES = 30_000
SAMPLE_MIN_NODES = 15_000
SAMPLE_SEED = 42

# Forest-fire sampling (Leskovec & Faloutsos 2006): a randomized variant of
# BFS/snowball sampling that avoids over-representing dense hub neighborhoods.
# Plain BFS/snowball keeps walking into whatever is locally dense, which
# would inflate exactly what our 1/2/3-hop traversal benchmark measures. The
# specific probabilities below are not load-bearing — what matters, and what
# prepare_dataset.py reports, is the *resulting* graph's degree distribution,
# connected components, and average/median/max degree.
FOREST_FIRE_FORWARD_P = 0.35   # burn probability along outgoing (citing) edges
FOREST_FIRE_BACKWARD_P = 0.20  # lower burn probability along incoming (cited-by) edges
FOREST_FIRE_NUM_SEEDS = 8      # ambassador restarts if one fire dies out early

# A node with 0 outgoing edges makes 1/2/3-hop traversals trivially instant —
# that measures nothing. Traversal start nodes are drawn only from nodes
# meeting this minimum out-degree; point-lookup start nodes have no such
# restriction (a lookup is valid regardless of degree).
QUERY_MIN_OUT_DEGREE = 1

# Deterministic query start-nodes are generated once and persisted here so
# every platform is queried against the *identical* node sequence, not just
# the same random seed (seeds alone don't guarantee identity across separate
# process runs on separate days once the candidate pool changes). Structure:
#   {"point_lookup": [id, ...], "traversal": [id, ...]}
QUERY_SAMPLE_FILE = DATA_PROCESSED / "query_start_nodes.json"


# --------------------------------------------------------------------------
# Platforms
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PlatformSpec:
    id: str
    name: str
    deployment: str          # "managed-free-tier" | "self-hosted-capped"
    driver: str               # "bolt" | "falkordb" | "arangodb"
    query_language: str
    advertised_vcpu: str
    advertised_ram: str
    advertised_disk: str
    region_note: str
    required_env: tuple[str, ...]
    optional_env: tuple[str, ...] = field(default_factory=tuple)

    def env_ready(self) -> bool:
        return all(os.getenv(k) for k in self.required_env)

    def connection(self) -> dict:
        return {k: os.getenv(k, "") for k in (*self.required_env, *self.optional_env)}


PLATFORMS: dict[str, PlatformSpec] = {
    "cognodb": PlatformSpec(
        id="cognodb",
        name="CognoDB Cloud",
        deployment="managed-free-tier",
        driver="bolt",
        query_language="Cypher",
        # 512 MB observed on the actual provisioned c0 console at benchmark
        # time, vs. 256 MB stated in the assignment PDF — see README caveats.
        advertised_vcpu="0.5 (burstable)",
        advertised_ram="512 MB",
        advertised_disk="1 GB",
        region_note="c0 free instance; benchmark client region documented in README",
        required_env=("COGNODB_URI", "COGNODB_USER", "COGNODB_PASSWORD"),
    ),
    "aura": PlatformSpec(
        id="aura",
        name="Neo4j AuraDB Free",
        deployment="managed-free-tier",
        driver="bolt",
        query_language="Cypher",
        advertised_vcpu="not disclosed (shared)",
        advertised_ram="not disclosed (Neo4j caps by node/rel count, not RAM)",
        advertised_disk="not disclosed",
        region_note="Free tier; benchmark client region documented in README",
        required_env=("AURA_URI", "AURA_USER", "AURA_PASSWORD"),
    ),
    "falkordb": PlatformSpec(
        id="falkordb",
        name="FalkorDB Cloud (Free)",
        deployment="managed-free-tier",
        driver="falkordb",
        query_language="Cypher (openCypher subset)",
        advertised_vcpu="shared",
        advertised_ram="100 MB",
        advertised_disk="shared (RAM-backed)",
        region_note="Free cloud instance; benchmark client region documented in README",
        required_env=("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_PASSWORD"),
        optional_env=("FALKORDB_GRAPH",),
    ),
    "memgraph": PlatformSpec(
        id="memgraph",
        name="Memgraph (self-hosted, capped)",
        deployment="self-hosted-capped",
        driver="bolt",
        query_language="Cypher (openCypher)",
        advertised_vcpu="0.5 (docker --cpus=0.5, matches CognoDB)",
        advertised_ram="512 MB (docker -m 512m, matches CognoDB)",
        # No enforced filesystem quota — docker volumes are host-backed, not
        # capacity-limited, unless we add an explicit quota. Don't claim a
        # cap we don't enforce; report actual stored size instead.
        advertised_disk="host-backed, not quota-limited; benchmark dataset kept below 1 GB",
        # NOTE: whether this is "localhost" or a real network hop depends on
        # where docker/docker-compose.yml is actually run — see README
        # "client machine and region" section. Do not assume localhost.
        region_note="Docker host documented explicitly in README run log",
        required_env=("MEMGRAPH_URI",),
        optional_env=("MEMGRAPH_USER", "MEMGRAPH_PASSWORD"),
    ),
    "arangodb": PlatformSpec(
        id="arangodb",
        name="ArangoDB (self-hosted, capped)",
        deployment="self-hosted-capped",
        driver="arangodb",
        query_language="AQL",
        advertised_vcpu="0.5 (docker --cpus=0.5, matches CognoDB)",
        advertised_ram="512 MB (docker -m 512m, matches CognoDB)",
        advertised_disk="host-backed, not quota-limited; benchmark dataset kept below 1 GB",
        region_note="Docker host documented explicitly in README run log",
        required_env=("ARANGO_URL", "ARANGO_USER", "ARANGO_PASSWORD", "ARANGO_DB"),
    ),
}


def available_platforms() -> list[PlatformSpec]:
    """Platforms whose required env vars are all set — lets the harness run
    against whichever accounts currently exist without editing code."""
    return [p for p in PLATFORMS.values() if p.env_ready()]


def get_platform(platform_id: str) -> PlatformSpec:
    if platform_id not in PLATFORMS:
        raise KeyError(f"Unknown platform '{platform_id}'. Known: {list(PLATFORMS)}")
    return PLATFORMS[platform_id]


# --------------------------------------------------------------------------
# Workload parameters
# --------------------------------------------------------------------------
WARMUP_ITERATIONS = 20
MEASURED_ITERATIONS = 150          # >= 100 per assignment spec
HOP_DEPTHS = (1, 2, 3)
CONCURRENCY_LEVELS = (1, 10, 20, 40)
MIXED_WORKLOAD_DURATION_SEC = 60
# Mixed workload composition is fixed and identical across platforms so the
# only variable under test is the database, not the query mix:
#   reads (80% of ops):  50% point lookup, 30% 1-hop traversal, 20% 2-hop traversal
#   writes (20% of ops): property UPDATE on an existing node (never create/delete),
#                         so node/edge counts never drift across a run.
MIXED_WORKLOAD_READ_RATIO = 0.8
MIXED_READ_MIX = {"point_lookup": 0.5, "hop1": 0.3, "hop2": 0.2}
MIXED_WRITE_PROPERTY = "benchmark_counter"
# Writes mutate MIXED_WRITE_PROPERTY, so graph *state* (not just counts) would
# drift between successive concurrency-sweep runs unless it's reset. The
# runner clears the property on every node before each mixed-workload run.
RESET_BETWEEN_MIXED_RUNS = True

# Same batch size for every platform by default — batch size is a variable
# under test (loader throughput), not something we hand-tune per platform.
# Override per-run with LOAD_BATCH_SIZE=200 if a specific tier can't keep up;
# any such override must be recorded in the results JSON and README.
LOAD_BATCH_SIZE = int(os.getenv("LOAD_BATCH_SIZE", "500"))

RANDOM_QUERY_SEED = 1337
