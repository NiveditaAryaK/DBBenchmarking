"""Fetch the raw SNAP cit-HepPh files. Idempotent — skips files already on disk.

Usage: python -m data.download
"""
from __future__ import annotations

import gzip
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from benchmark import config


def _fetch_gz(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"[skip] {dest} already present ({dest.stat().st_size:,} bytes)")
        return
    tmp_gz = dest.with_suffix(dest.suffix + ".gz.tmp")
    print(f"[fetch] {url}")
    # Shells out to curl rather than urllib: some sandboxed/corporate
    # networks terminate TLS with a proxy CA that isn't in Python's certifi
    # bundle but is in the system trust store curl uses.
    subprocess.run(["curl", "-fsSL", "--max-time", "120", "-o", str(tmp_gz), url], check=True)
    with gzip.open(tmp_gz, "rb") as f_in, open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp_gz.unlink()
    print(f"[done] {dest} ({dest.stat().st_size:,} bytes)")


def main() -> None:
    _fetch_gz(config.DATASET_EDGES_URL, config.DATASET_RAW_EDGES_FILE)
    _fetch_gz(config.DATASET_DATES_URL, config.DATASET_RAW_DATES_FILE)


if __name__ == "__main__":
    main()
