"""SQLite storage with separate real/synthetic observations and text-bound caches.

Legacy yield tables are migrated without dropping observations. Legacy extraction
rows without a text digest cannot be used for real scoring; import them again
through the validated extraction path. Changing text or extraction invalidates
all scores for that source, because adjacent-statement changes also become stale.
"""

import datetime
import hashlib
import json
import math
import sqlite3

DEFAULT_DB = "fomc.db"
SOURCE_FLAGS = {"FOMC": 0, "SYNTHETIC": 1}


def connect(path=DEFAULT_DB):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def source_flag(source):
    if source not in SOURCE_FLAGS:
        raise ValueError("source must be FOMC or SYNTHETIC")
    return SOURCE_FLAGS[source]


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _date(value):
    if not isinstance(value, str) or datetime.date.fromisoformat(value).isoformat() != value:
        raise ValueError("date must be ISO YYYY-MM-DD")


def _flag(value):
    if value not in (0, 1):
        raise ValueError("is_synthetic must be 0 or 1")
    return int(value)


def init_db(conn):
    """Create storage and migrate the old date-only yield primary key."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL, date TEXT NOT NULL, title TEXT, url TEXT,
            text TEXT NOT NULL, is_synthetic INTEGER NOT NULL,
            UNIQUE(source, date),
            CHECK ((source='FOMC' AND is_synthetic=0) OR
                   (source='SYNTHETIC' AND is_synthetic=1))
        );
        CREATE TABLE IF NOT EXISTS extractions (
            statement_id INTEGER NOT NULL, cache_key TEXT NOT NULL,
            model TEXT NOT NULL, schema_version TEXT NOT NULL,
            raw_response TEXT, record_json TEXT NOT NULL, created_at TEXT NOT NULL,
            text_sha256 TEXT, prompt_version TEXT, provenance_json TEXT,
            PRIMARY KEY (statement_id, cache_key),
            FOREIGN KEY (statement_id) REFERENCES statements(id)
        );
        CREATE TABLE IF NOT EXISTS scores (
            statement_id INTEGER PRIMARY KEY, index_value REAL NOT NULL,
            prior_statement_id INTEGER, delta_index REAL, change_vector_json TEXT,
            extraction_cache_key TEXT, model TEXT, schema_version TEXT,
            text_sha256 TEXT,
            FOREIGN KEY (statement_id) REFERENCES statements(id),
            FOREIGN KEY (prior_statement_id) REFERENCES statements(id)
        );
        CREATE TABLE IF NOT EXISTS yields (
            date TEXT NOT NULL, dgs2 REAL, dgs10 REAL,
            is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0,1)),
            PRIMARY KEY (date, is_synthetic)
        );
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_label TEXT NOT NULL,
            metric TEXT NOT NULL, value REAL, n INTEGER,
            created_at TEXT NOT NULL, provenance_json TEXT
        );
    """)
    with conn:
        additions = {
            "extractions": ("text_sha256", "prompt_version", "provenance_json"),
            "scores": ("extraction_cache_key", "model", "schema_version", "text_sha256"),
            "results": ("provenance_json",),
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        primary = {row["name"]: row["pk"] for row in conn.execute("PRAGMA table_info(yields)") if row["pk"]}
        if primary != {"date": 1, "is_synthetic": 2}:
            conn.execute("ALTER TABLE yields RENAME TO yields_legacy")
            conn.execute("CREATE TABLE yields (date TEXT NOT NULL, dgs2 REAL, dgs10 REAL, "
                         "is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0,1)), "
                         "PRIMARY KEY (date, is_synthetic))")
            conn.execute("INSERT INTO yields SELECT date, dgs2, dgs10, is_synthetic FROM yields_legacy")
            conn.execute("DROP TABLE yields_legacy")
        # Apply provenance constraints to legacy statement tables as well.
        for operation in ("INSERT", "UPDATE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS statements_provenance_{operation.lower()}
                BEFORE {operation} ON statements
                WHEN NOT ((NEW.source='FOMC' AND NEW.is_synthetic=0) OR
                          (NEW.source='SYNTHETIC' AND NEW.is_synthetic=1))
                BEGIN SELECT RAISE(ABORT, 'statement provenance mismatch'); END""")


def _invalidate_scores(conn, source):
    conn.execute("DELETE FROM scores WHERE statement_id IN "
                 "(SELECT id FROM statements WHERE source=?)", (source,))


def upsert_statement(conn, source, date, text, title=None, url=None,
                     is_synthetic=False):
    if source_flag(source) != _flag(is_synthetic):
        raise ValueError("statement source/is_synthetic mismatch")
    _date(date)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("statement text must be nonempty")
    with conn:
        old = conn.execute("SELECT id, text, is_synthetic FROM statements WHERE source=? AND date=?",
                           (source, date)).fetchone()
        if old and old["is_synthetic"] != source_flag(source):
            raise ValueError("stored statement provenance mismatch")
        if old is None or old["text"] != text:
            _invalidate_scores(conn, source)
            if old:
                conn.execute("DELETE FROM extractions WHERE statement_id=?", (old["id"],))
        conn.execute("""INSERT INTO statements (source,date,title,url,text,is_synthetic)
            VALUES (?,?,?,?,?,?) ON CONFLICT(source,date) DO UPDATE SET
            title=excluded.title, url=excluded.url, text=excluded.text""",
                     (source, date, title, url, text, source_flag(source)))
        return conn.execute("SELECT id FROM statements WHERE source=? AND date=?",
                            (source, date)).fetchone()["id"]


def get_statements(conn, source=None):
    if source is not None:
        source_flag(source)
        return conn.execute("SELECT * FROM statements WHERE source=? ORDER BY date", (source,)).fetchall()
    return conn.execute("SELECT * FROM statements ORDER BY date, source").fetchall()


def get_cached_extraction(conn, statement_id, cache_key):
    row = conn.execute("SELECT e.record_json, e.text_sha256, s.text FROM extractions e "
                       "JOIN statements s ON s.id=e.statement_id "
                       "WHERE statement_id=? AND cache_key=?", (statement_id, cache_key)).fetchone()
    if row is None or row["text_sha256"] != text_digest(row["text"]):
        return None
    return json.loads(row["record_json"])


def save_extraction(conn, statement_id, cache_key, model, schema_version,
                    raw_response, record, created_at, *, text_sha256=None,
                    prompt_version=None, provenance=None):
    statement = conn.execute("SELECT * FROM statements WHERE id=?", (statement_id,)).fetchone()
    if statement is None:
        raise ValueError("unknown statement_id")
    if statement["is_synthetic"] != source_flag(statement["source"]):
        raise ValueError("stored statement provenance mismatch")
    if not isinstance(model, str) or not model.strip() or not isinstance(schema_version, str) or not schema_version.strip():
        raise ValueError("extraction model and schema_version are required")
    synthetic = statement["source"] == "SYNTHETIC"
    if synthetic != (model.upper() == "SYNTHETIC") or synthetic != (schema_version.lower() == "synthetic"):
        raise ValueError("extraction model/schema provenance mismatch")
    digest = text_digest(statement["text"])
    if text_sha256 is not None and text_sha256 != digest:
        raise ValueError("extraction text SHA256 does not match statement")
    record_json = json.dumps(record, allow_nan=False)
    provenance_json = json.dumps(provenance, allow_nan=False) if provenance is not None else None
    with conn:
        conn.execute("""INSERT OR REPLACE INTO extractions
            (statement_id,cache_key,model,schema_version,raw_response,record_json,
             created_at,text_sha256,prompt_version,provenance_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (statement_id, cache_key, model, schema_version, raw_response,
                      record_json, created_at, digest, prompt_version, provenance_json))
        _invalidate_scores(conn, statement["source"])


def save_score(conn, statement_id, index_value, prior_statement_id,
               delta, change_vector, *, extraction_cache_key=None, model=None,
               schema_version=None, text_sha256=None, commit=True):
    conn.execute("""INSERT OR REPLACE INTO scores
        (statement_id,index_value,prior_statement_id,delta_index,change_vector_json,
         extraction_cache_key,model,schema_version,text_sha256)
        VALUES (?,?,?,?,?,?,?,?,?)""",
                 (statement_id, index_value, prior_statement_id, delta,
                  json.dumps(change_vector, allow_nan=False) if change_vector is not None else None,
                  extraction_cache_key, model, schema_version, text_sha256))
    if commit:
        conn.commit()


def upsert_yield(conn, date, dgs2, dgs10, is_synthetic):
    _date(date)
    flag = _flag(is_synthetic)
    for value in (dgs2, dgs10):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError("yield must be finite numeric percent or None")
    with conn:
        conn.execute("INSERT OR REPLACE INTO yields (date,dgs2,dgs10,is_synthetic) VALUES (?,?,?,?)",
                     (date, dgs2, dgs10, flag))


def save_result(conn, run_label, metric, value, n, created_at, *, provenance=None,
                commit=True):
    conn.execute("INSERT INTO results (run_label,metric,value,n,created_at,provenance_json) "
                 "VALUES (?,?,?,?,?,?)",
                 (run_label, metric, value, n, created_at,
                  json.dumps(provenance, allow_nan=False) if provenance is not None else None))
    if commit:
        conn.commit()
