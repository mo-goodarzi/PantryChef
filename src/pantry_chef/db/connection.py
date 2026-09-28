"""SQLite connection helpers."""

import sqlite3
from importlib.resources import files
from pathlib import Path


def connect(db_path: Path | str, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and rows accessible by column name.

    check_same_thread=False lets a server share the connection between worker threads;
    the caller must then make sure only one thread uses it at a time (the API uses a lock).
    """
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes from schema.sql."""
    schema = files("pantry_chef.db").joinpath("schema.sql").read_text()
    conn.executescript(schema)
