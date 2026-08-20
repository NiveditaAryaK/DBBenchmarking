.PHONY: setup dataset load bench all report clean-results docker-up docker-down docker-reset

VENV := .venv/bin
PY := $(VENV)/python

# One-time setup: virtualenv + pinned dependencies.
setup:
	python3 -m venv .venv
	$(VENV)/pip install --quiet --upgrade pip
	$(VENV)/pip install --quiet -r requirements.txt

# Fetch the raw SNAP files and build the forest-fire-sampled dataset.
# Safe to re-run: download.py skips files already on disk.
dataset:
	$(PY) -m data.download
	$(PY) -m data.prepare_dataset

# Start the resource-capped self-hosted comparators (Memgraph, ArangoDB).
docker-up:
	docker compose -f docker/docker-compose.yml up -d

docker-down:
	docker compose -f docker/docker-compose.yml down

# PLATFORM=<id>|all  (default: all platforms with credentials in .env)
PLATFORM ?= all

load:
	$(PY) -m benchmark.runner load --platform $(PLATFORM)

bench:
	$(PY) -m benchmark.runner bench --platform $(PLATFORM)

# Load then bench, for every platform with credentials configured.
all:
	$(PY) -m benchmark.runner all --platform $(PLATFORM)

# Regenerate README result tables + charts from results/raw/*.json.
report:
	$(PY) -m scripts.generate_report

clean-results:
	rm -f results/raw/*.json results/charts/*.png
