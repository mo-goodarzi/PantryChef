"""SQLite connection helpers."""

import sqlite3
from importlib.resources import files
from pathlib import Path


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and rows accessible by column name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes from schema.sql."""
    schema = files("pantry_chef.db").joinpath("schema.sql").read_text()
    conn.executescript(schema)
