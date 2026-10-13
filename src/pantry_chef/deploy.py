"""Deployment helpers for the Hugging Face Space (docs/decisions.md 2026-10-13).

The Space's disk is wiped on every restart, and the recipe data (Kaggle Food.com files,
owned by others) is never in a public repo: it lives in a PRIVATE dataset repo and is
downloaded when the container starts. The same module builds the files that go there.

    python -m pantry_chef.deploy fetch     # in the Space, before the API starts
"""

import argparse
import os
import shutil
import sqlite3
from pathlib import Path

from pantry_chef.db.state import open_state_db

# What the app needs, relative to data/processed/. state.db is a seed holding only paid
# caches (ingredient matcher, video answers): no profiles, conversations or spend.
DATA_FILES = ["pantry.db", "allergy_reviews.json", "hidden_allergens.json", "state.db"]
DATA_DIRS = ["chroma"]
SEED_TABLES = ["match_cache", "video_cache"]


def seed_state(source: Path, dest: Path) -> dict[str, int]:
    """A fresh state.db with only the cache tables copied from `source`."""
    dest.unlink(missing_ok=True)
    seed = open_state_db(dest)
    try:
        seed.execute("ATTACH DATABASE ? AS src", (str(source),))
        copied = {}
        for table in SEED_TABLES:
            with seed:
                seed.execute(f"INSERT INTO {table} SELECT * FROM src.{table}")
            copied[table] = seed.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        seed.execute("DETACH DATABASE src")
    finally:
        seed.close()
    return copied


def stage_data(processed: Path, staging: Path) -> list[str]:
    """Copy what the Space needs into `staging` (state.db as a cache-only seed)."""
    staging.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        if name == "state.db":
            seed_state(processed / name, staging / name)
        else:
            shutil.copy2(processed / name, staging / name)
    for name in DATA_DIRS:
        shutil.copytree(processed / name, staging / name, dirs_exist_ok=True)
    return sorted(p.name for p in staging.iterdir())


def fetch(repo_id: str, dest: Path, token: str | None) -> bool:
    """Download the data repo into `dest` unless it is already there. True if downloaded.
    An existing state.db is kept (it has this run's conversations and spend)."""
    if (dest / "pantry.db").exists() and (dest / "chroma").exists():
        return False
    from huggingface_hub import snapshot_download

    keep_state = (dest / "state.db").exists()
    snapshot_download(
        repo_id,
        repo_type="dataset",
        local_dir=dest,
        token=token,
        ignore_patterns=["state.db"] if keep_state else None,
    )
    return True


def check_sqlite(path: Path) -> None:
    """Fail early with a clear message if a downloaded database is unreadable."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hugging Face Space data")
    parser.add_argument("command", choices=["fetch"])
    parser.add_argument("--dest", type=Path, default=Path("data/processed"))
    args = parser.parse_args()
    repo_id = os.environ["DATA_REPO"]  # e.g. <user>/pantry-chef-data (private)
    downloaded = fetch(repo_id, args.dest, os.environ.get("HF_TOKEN"))
    check_sqlite(args.dest / "pantry.db")
    print(f"data {'downloaded from ' + repo_id if downloaded else 'already present'}")


if __name__ == "__main__":
    main()
