"""Durable import journal; file copying will supply verification evidence later."""

import sqlite3
from datetime import datetime, timezone


SCHEMA_VERSION = 2


def migrate(connection: sqlite3.Connection) -> None:
    """Add the journal transactionally without modifying legacy archive rows.

    Version 1 databases predate PRAGMA user_version. Their imports table is the
    fallback version marker. The caller must commit outstanding work first.
    """
    if connection.in_transaction:
        raise ValueError("Migration requires a connection without an active transaction")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if 'imports' not in tables:
                raise ValueError("Expected an existing version 1 archive database")
            versions = {row[0] for row in connection.execute(
                "SELECT schema_version FROM imports"
            )}
            if versions - {1}:
                raise ValueError("Unsupported legacy schema version")
            version = 1
        if version not in (1, SCHEMA_VERSION):
            raise ValueError(f"Unsupported schema version: {version}")
        if version == 1:
            # Separate job records do not falsely mark source-only imports as copied.
            connection.execute("""CREATE TABLE import_jobs (
                id TEXT PRIMARY KEY,
                source_root TEXT NOT NULL,
                destination_root TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'running'
                    CHECK(state IN ('running','paused','failed','completed')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )""")
            connection.execute("""CREATE TABLE import_files (
                id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES import_jobs(id),
                source_relative_path TEXT NOT NULL,
                stored_relative_path TEXT NOT NULL,
                expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
                source_mtime_ns INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending','copying','verified','failed')),
                actual_size INTEGER,
                source_sha256 TEXT,
                destination_sha256 TEXT,
                updated_at TEXT NOT NULL,
                error TEXT,
                UNIQUE(job_id, source_relative_path),
                CHECK(state != 'verified' OR (
                    actual_size IS NOT NULL AND actual_size = expected_size
                    AND source_sha256 IS NOT NULL
                    AND length(source_sha256) = 64
                    AND source_sha256 NOT GLOB '*[^0-9a-f]*'
                    AND destination_sha256 IS NOT NULL
                    AND source_sha256 = destination_sha256
                ))
            )""")
            connection.execute("PRAGMA user_version = 2")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class ImportJournal:
    """Commit each checkpoint independently so completed progress survives restart.

    Own a dedicated connection: these methods commit their transactions. Resume
    and the copy service must recheck source snapshots and destination bytes.
    This journal stores evidence; it does not itself verify filesystem contents.
    """

    def __init__(self, connection: sqlite3.Connection):
        migrate(connection)
        self.connection = connection

    def create_job(self, job_id: str, source_root: str, destination_root: str) -> None:
        now = _now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO import_jobs(id,source_root,destination_root,created_at,updated_at) "
                "VALUES (?,?,?,?,?)", (job_id, source_root, destination_root, now, now)
            )

    def add_file(self, job_id: str, source_path: str, stored_path: str,
                 size: int, mtime_ns: int) -> int:
        with self.connection:
            self._require_running(job_id)
            cursor = self.connection.execute(
                "INSERT INTO import_files(job_id,source_relative_path,stored_relative_path,"
                "expected_size,source_mtime_ns,updated_at) VALUES (?,?,?,?,?,?)",
                (job_id, source_path, stored_path, size, mtime_ns, _now())
            )
            return cursor.lastrowid

    def transition_file(self, file_id: int, state: str, *, actual_size=None,
                        source_sha256=None, destination_sha256=None, error=None) -> None:
        """Record copy attempts; SQLite rejects incomplete verification evidence."""
        allowed = {'pending': {'copying'}, 'copying': {'verified', 'failed'},
                   'failed': {'copying'}, 'verified': {'copying'}}
        with self.connection:
            # Acquire the writer lock before reading the current state.
            self.connection.execute('BEGIN IMMEDIATE')
            row = self.connection.execute(
                'SELECT job_id,state FROM import_files WHERE id=?', (file_id,)
            ).fetchone()
            if row is None:
                raise ValueError('Unknown file')
            self._require_running(row[0])
            if state not in allowed[row[1]]:
                raise ValueError(f'Invalid file transition: {row[1]} -> {state}')
            self.connection.execute(
                'UPDATE import_files SET state=?,actual_size=?,source_sha256=?,'
                'destination_sha256=?,error=?,updated_at=? WHERE id=?',
                (state, actual_size, source_sha256, destination_sha256, error, _now(), file_id)
            )

    def transition_job(self, job_id: str, state: str, *, error=None) -> None:
        allowed = {'running': {'paused', 'failed', 'completed'},
                   'paused': {'running'}, 'failed': {'running'}, 'completed': set()}
        with self.connection:
            self.connection.execute('BEGIN IMMEDIATE')
            row = self.connection.execute(
                'SELECT state FROM import_jobs WHERE id=?', (job_id,)
            ).fetchone()
            if row is None or state not in allowed[row[0]]:
                raise ValueError('Invalid job transition')
            if state == 'completed' and self.connection.execute(
                "SELECT 1 FROM import_files WHERE job_id=? AND state!='verified' LIMIT 1",
                (job_id,)
            ).fetchone():
                raise ValueError('Unverified files remain')
            if state == 'running':
                # An interrupted file restarts at its beginning, never mid-hash.
                self.connection.execute(
                    "UPDATE import_files SET state='pending',actual_size=NULL,"
                    "source_sha256=NULL,destination_sha256=NULL,updated_at=? "
                    "WHERE job_id=? AND state='copying'", (_now(), job_id)
                )
            self.connection.execute(
                'UPDATE import_jobs SET state=?,error=?,updated_at=? WHERE id=?',
                (state, error, _now(), job_id)
            )

    def _require_running(self, job_id: str) -> None:
        row = self.connection.execute(
            'SELECT state FROM import_jobs WHERE id=?', (job_id,)
        ).fetchone()
        if row is None or row[0] != 'running':
            raise ValueError('Job must be running')
