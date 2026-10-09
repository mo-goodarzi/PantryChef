"""Runtime state database (state.db): match cache and consented profiles.

Kept apart from the recipe database so that rebuilding recipes never deletes user data
or paid LLM answers, and so a deployment can ship the recipe database read-only.
"""

import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from pantry_chef.db.connection import connect
from pantry_chef.models.profile import UserProfile
from pantry_chef.observability import hash_user_id


def open_state_db(path: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (and create if needed) the state database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path, check_same_thread=check_same_thread)
    conn.executescript(state_schema())
    migrate_match_cache(conn)
    return conn


def state_schema() -> str:
    return files("pantry_chef.db").joinpath("state_schema.sql").read_text()


# The matcher model before match_cache sources named their model: every older answer
# (in pantry.db or an older state.db) was made with it, so it is tagged with it.
LEGACY_MATCH_MODEL = "gpt-5.4-mini"


def migrate_match_cache(conn: sqlite3.Connection) -> None:
    """Rebuild a match_cache keyed by the pair alone into one keyed by pair and source,
    tagging the old sources with LEGACY_MATCH_MODEL. Does nothing on a current table."""
    key = [r["name"] for r in conn.execute("PRAGMA table_info(match_cache)") if r["pk"]]
    if "source" in key:
        return
    try:
        # one transaction: a failure leaves the old table as it was
        conn.executescript(
            "BEGIN;"
            "ALTER TABLE match_cache RENAME TO match_cache_old;"
            f"{state_schema()};"
            "INSERT INTO match_cache SELECT user_term, recipe_term, label, "
            f"source || ':{LEGACY_MATCH_MODEL}', created_at FROM match_cache_old;"
            "DROP TABLE match_cache_old;"
            "COMMIT;"
        )
    except sqlite3.Error:
        conn.rollback()
        raise


def import_legacy_match_cache(state: sqlite3.Connection, recipe_db: Path | str) -> int:
    """Copy match_cache rows from a recipe database built before state.db existed,
    tagged with LEGACY_MATCH_MODEL so that only a matcher on that model reuses them.

    Idempotent (existing pairs are kept); rows with NULL keys are skipped. Returns the
    number of rows added.
    """
    if not Path(recipe_db).exists():
        return 0
    state.execute("ATTACH DATABASE ? AS legacy", (str(recipe_db),))
    try:
        has_table = state.execute(
            "SELECT 1 FROM legacy.sqlite_master WHERE type = 'table' AND name = 'match_cache'"
        ).fetchone()
        if not has_table:
            return 0
        with state:
            cursor = state.execute(
                "INSERT OR IGNORE INTO match_cache "
                "SELECT user_term, recipe_term, label, source || ':' || ?, created_at "
                "FROM legacy.match_cache "
                "WHERE user_term IS NOT NULL AND recipe_term IS NOT NULL "
                "AND label IS NOT NULL AND source IS NOT NULL",
                (LEGACY_MATCH_MODEL,),
            )
        return cursor.rowcount
    finally:
        state.execute("DETACH DATABASE legacy")


class ProfileStore:
    """Profiles keyed by a hashed user id; saved only when the user consented."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def load(self, user_id: str) -> UserProfile | None:
        row = self.conn.execute(
            "SELECT profile_json FROM profiles WHERE user_id = ?", (hash_user_id(user_id),)
        ).fetchone()
        return UserProfile.model_validate_json(row["profile_json"]) if row else None

    def save(self, user_id: str, profile: UserProfile) -> bool:
        """Store the profile; returns False (and stores nothing) without consent."""
        if not profile.consent_to_store:
            return False
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO profiles (user_id, profile_json, updated_at) "
                "VALUES (?, ?, ?)",
                (
                    hash_user_id(user_id),
                    profile.model_dump_json(),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
        return True

    def delete(self, user_id: str) -> bool:
        """ "Delete my data". Returns whether a profile existed."""
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM profiles WHERE user_id = ?", (hash_user_id(user_id),)
            )
        return cursor.rowcount > 0
