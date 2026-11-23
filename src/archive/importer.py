"""Import normalized messages and source asset metadata into SQLite."""

from __future__ import annotations

from contextlib import closing
import json
import mimetypes
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
FILENAME_TIMESTAMP_RE = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})(?:_(?P<sequence>\d+))?(?:\.[^.]+)?$"
)

ASSET_TYPES = {
    "photo": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"},
    "video": {".mp4", ".mov", ".m4v"},
    "audio": {".m4a", ".mp3", ".wav", ".aac"},
    "document": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".hwp"},
    "contact": {".vcf"},
    "text_file": {".txt", ".csv"},
}

SCHEMA_SQL = """
CREATE TABLE imports (
    id INTEGER PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    source_root_path TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    asset_count INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id),
    local_id TEXT NOT NULL,
    timestamp TEXT,
    sender TEXT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_file TEXT,
    source_line_start INTEGER,
    source_line_end INTEGER,
    source_record_number INTEGER,
    metadata_json TEXT NOT NULL,
    UNIQUE(import_id, local_id)
);

CREATE INDEX messages_timestamp_idx ON messages(timestamp, id);

CREATE TABLE assets (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id),
    local_id TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    original_relative_path TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    mime_type TEXT,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    filename_timestamp TEXT,
    filename_sequence INTEGER,
    metadata_json TEXT NOT NULL,
    UNIQUE(import_id, local_id),
    UNIQUE(import_id, original_relative_path)
);

CREATE INDEX assets_filename_timestamp_idx
    ON assets(filename_timestamp, filename_sequence, original_relative_path);
"""


def import_archive(
    messages: Sequence[Mapping[str, Any]],
    assets_root: str | Path,
    database_path: str | Path,
    *,
    source_metadata: Mapping[str, Any] | None = None,
) -> Path:
    """Create a new archive database from normalized messages and asset files.

    Asset bytes stay in ``assets_root``. The database stores source-relative paths
    and metadata only. Existing database files are rejected to avoid accidental
    replacement of an archive.
    """

    root = Path(assets_root).expanduser().resolve()
    destination = Path(database_path).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Asset root is not a directory: {root}")
    if destination.exists():
        raise FileExistsError(f"Archive database already exists: {destination}")

    normalized_messages = list(messages)
    assets = list(_discover_assets(root, excluded_path=destination))
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with closing(sqlite3.connect(destination)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cursor = connection.execute(
                """INSERT INTO imports (
                       schema_version, source_root_path, imported_at,
                       message_count, asset_count, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    SCHEMA_VERSION,
                    str(root),
                    imported_at,
                    len(normalized_messages),
                    len(assets),
                    _json(source_metadata or {}),
                ),
            )
            import_id = cursor.lastrowid
            _insert_messages(connection, import_id, normalized_messages)
            _insert_assets(connection, import_id, assets)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return destination


def _discover_assets(root: Path, *, excluded_path: Path) -> Iterable[dict[str, Any]]:
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.resolve() != excluded_path),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for index, path in enumerate(files, start=1):
        relative_path = path.relative_to(root).as_posix()
        extension = path.suffix.lower()
        filename_timestamp, filename_sequence = _filename_metadata(path.name)
        yield {
            "local_id": f"asset_{index:06d}",
            "original_filename": path.name,
            "original_relative_path": relative_path,
            "asset_type": _asset_type(extension),
            "mime_type": mimetypes.guess_type(path.name)[0],
            "extension": extension,
            "size_bytes": path.stat().st_size,
            "filename_timestamp": filename_timestamp,
            "filename_sequence": filename_sequence,
            "metadata_json": "{}",
        }


def _insert_messages(
    connection: sqlite3.Connection,
    import_id: int,
    messages: Sequence[Mapping[str, Any]],
) -> None:
    for index, message in enumerate(messages, start=1):
        connection.execute(
            """INSERT INTO messages (
                   import_id, local_id, timestamp, sender, type, content,
                   source_file, source_line_start, source_line_end,
                   source_record_number, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                import_id,
                message.get("localId") or f"msg_{index:06d}",
                message.get("timestamp"),
                message.get("sender"),
                message.get("type", "text"),
                message.get("content", ""),
                message.get("source_file"),
                message.get("source_line_start"),
                message.get("source_line_end"),
                message.get("source_record_number"),
                _json(message.get("metadata") or {}),
            ),
        )


def _insert_assets(
    connection: sqlite3.Connection, import_id: int, assets: Sequence[Mapping[str, Any]]
) -> None:
    connection.executemany(
        """INSERT INTO assets (
               import_id, local_id, original_filename, original_relative_path,
               asset_type, mime_type, extension, size_bytes, filename_timestamp,
               filename_sequence, metadata_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                import_id,
                asset["local_id"],
                asset["original_filename"],
                asset["original_relative_path"],
                asset["asset_type"],
                asset["mime_type"],
                asset["extension"],
                asset["size_bytes"],
                asset["filename_timestamp"],
                asset["filename_sequence"],
                asset["metadata_json"],
            )
            for asset in assets
        ],
    )


def _filename_metadata(filename: str) -> tuple[str | None, int | None]:
    match = FILENAME_TIMESTAMP_RE.match(filename)
    if not match:
        return None, None
    try:
        timestamp = datetime.strptime(
            match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
        ).isoformat(timespec="seconds")
    except ValueError:
        return None, None
    sequence = match.group("sequence")
    return timestamp, int(sequence) if sequence is not None else None


def _asset_type(extension: str) -> str:
    for asset_type, extensions in ASSET_TYPES.items():
        if extension in extensions:
            return asset_type
    return "unknown"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
