"""
db.py
=====

Everything persistent lives in one SQLite file (default: fomc.db). I chose
SQLite for three reasons:

  1. It is the natural fit -- the data is small, relational, and I want to JOIN
     statements to their extractions to their yields.
  2. It makes the pipeline reproducible: the expensive LLM calls are cached as
     rows, so a second run costs nothing and gives identical answers.
  3. It is SQL practice, which I want anyway.

Tables
------
  statements   one row per policy statement (real or synthetic)
  extractions  cached LLM output, keyed so we never re-bill for the same input
  scores       the computed index + change vs the prior statement
  yields       daily 2y / 10y treasury yields
  results      saved alignment numbers from analyze.py (so I can query history)

I use json.dumps for the few "bag of stuff" columns (quotes, change_vector,
raw LLM response). That keeps the schema simple without losing the detail.
"""

import sqlite3
import json

DEFAULT_DB = "fomc.db"


def connect(path=DEFAULT_DB):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # lets me read columns by name, like a dict
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn):
    """Create tables if they don't exist. Safe to call every run."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS statements (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source       TEXT NOT NULL,          -- 'FOMC' or 'SYNTHETIC'
            date         TEXT NOT NULL,          -- ISO date, YYYY-MM-DD
            title        TEXT,
            url          TEXT,
            text         TEXT NOT NULL,
            is_synthetic INTEGER NOT NULL,        -- 0 real, 1 synthetic
            UNIQUE(source, date)
        );

        CREATE TABLE IF NOT EXISTS extractions (
            statement_id   INTEGER NOT NULL,
            cache_key      TEXT NOT NULL,          -- sha256(model+schema+text)
            model          TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            raw_response   TEXT,                   -- exactly what the LLM returned
            record_json    TEXT NOT NULL,          -- parsed + validated record
            created_at     TEXT NOT NULL,
            PRIMARY KEY (statement_id, cache_key),
            FOREIGN KEY (statement_id) REFERENCES statements(id)
        );

        CREATE TABLE IF NOT EXISTS scores (
            statement_id       INTEGER PRIMARY KEY,
            index_value        REAL NOT NULL,
            prior_statement_id INTEGER,            -- NULL for the first statement
            delta_index        REAL,               -- NULL for the first statement
            change_vector_json TEXT,
            FOREIGN KEY (statement_id) REFERENCES statements(id),
            FOREIGN KEY (prior_statement_id) REFERENCES statements(id)
        );

        CREATE TABLE IF NOT EXISTS yields (
            date         TEXT PRIMARY KEY,         -- ISO date
            dgs2         REAL,
            dgs10        REAL,
            is_synthetic INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_label   TEXT NOT NULL,             -- 'real' or 'synthetic'
            metric      TEXT NOT NULL,             -- what was measured
            value       REAL,
            n           INTEGER,
            created_at  TEXT NOT NULL
        );
        """
    )
    conn.commit()


# ---- small insert/get helpers ----------------------------------------------

def upsert_statement(conn, source, date, text, title=None, url=None,
                     is_synthetic=False):
    """Insert a statement (ignore if the (source, date) pair already exists)
    and return its id."""
    conn.execute(
        "INSERT OR IGNORE INTO statements (source, date, title, url, text, is_synthetic)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (source, date, title, url, text, 1 if is_synthetic else 0),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM statements WHERE source=? AND date=?", (source, date)
    ).fetchone()
    return row["id"]


def get_statements(conn, source=None):
    """Return statements ordered by date (oldest first), optionally filtered."""
    if source:
        rows = conn.execute(
            "SELECT * FROM statements WHERE source=? ORDER BY date", (source,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM statements ORDER BY date").fetchall()
    return rows


def get_cached_extraction(conn, statement_id, cache_key):
    row = conn.execute(
        "SELECT record_json FROM extractions WHERE statement_id=? AND cache_key=?",
        (statement_id, cache_key),
    ).fetchone()
    return json.loads(row["record_json"]) if row else None


def save_extraction(conn, statement_id, cache_key, model, schema_version,
                    raw_response, record, created_at):
    conn.execute(
        "INSERT OR REPLACE INTO extractions"
        " (statement_id, cache_key, model, schema_version, raw_response,"
        "  record_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (statement_id, cache_key, model, schema_version, raw_response,
         json.dumps(record), created_at),
    )
    conn.commit()


def save_score(conn, statement_id, index_value, prior_statement_id,
               delta, change_vector):
    conn.execute(
        "INSERT OR REPLACE INTO scores"
        " (statement_id, index_value, prior_statement_id, delta_index,"
        "  change_vector_json) VALUES (?, ?, ?, ?, ?)",
        (statement_id, index_value, prior_statement_id, delta,
         json.dumps(change_vector) if change_vector is not None else None),
    )
    conn.commit()


def upsert_yield(conn, date, dgs2, dgs10, is_synthetic):
    conn.execute(
        "INSERT OR REPLACE INTO yields (date, dgs2, dgs10, is_synthetic)"
        " VALUES (?, ?, ?, ?)",
        (date, dgs2, dgs10, 1 if is_synthetic else 0),
    )
    conn.commit()


def save_result(conn, run_label, metric, value, n, created_at):
    conn.execute(
        "INSERT INTO results (run_label, metric, value, n, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (run_label, metric, value, n, created_at),
    )
    conn.commit()
