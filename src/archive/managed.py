"""Create and reopen self-contained archives using the existing parser CLI."""

import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .copying import (DEFAULT_CHUNK_BYTES, _copy_import, _hash_file, _reader,
                      _relative, _sync_directory, _writer_lock)
from .database import ImportJournal, SCHEMA_VERSION, _now
from .importer import (SCHEMA_SQL, _asset_type, _filename_metadata, _insert_assets,
                       _insert_messages, _json)
from .storage import DEFAULT_RESERVE_BYTES, build_inventory
from .filesystem import probe_storage, publish_no_replace, sync_file, recover_publications


def _connect(path: Path, *, readonly=False):
    if path.is_symlink():
        raise ValueError('Archive database must not be a symlink')
    connection = sqlite3.connect(path.as_uri() + '?mode=' + ('ro' if readonly else 'rw'), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def _bootstrap(source, root: Path, reserve_bytes: int) -> None:
    """Prepare a full database; fallback publication is recovered before opening it."""
    for entry in root.iterdir():
        if entry.name != '.import.lock' and not entry.name.startswith('.archive-init-'):
            raise FileExistsError('Destination contains unrelated files')
    inventory = build_inventory(source, root, reserve_bytes=reserve_bytes)
    report = {'files': [asdict(item) for item in inventory.files],
              'warnings': list(inventory.warnings)}
    report['storageCapabilities'] = probe_storage(root)
    if not report['storageCapabilities']['directorySync']:
        report['warnings'].append('directory_sync_unavailable_power_loss_durability_limited')
    fd, name = tempfile.mkstemp(prefix='.archive-init-', suffix='.sqlite', dir=root)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript(SCHEMA_SQL)
            journal = ImportJournal(connection)
            journal.register_inventory(
                str(uuid.uuid4()), str(inventory.source_root), str(root), inventory.files,
                archive_id=str(uuid.uuid4()), inventory_json=_json(report)
            )
        sync_file(temporary)
        publish_no_replace(temporary, root / 'archive.sqlite', recovery_root=root)
        _sync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_transcript(path: Path) -> dict:
    """Keep CLI compatibility without changing parser imports or global sys.path."""
    parser = Path(__file__).resolve().parents[1] / 'parser' / 'main.py'
    with tempfile.TemporaryDirectory(prefix='chat-parser-') as directory:
        output = Path(directory) / 'result.json'
        completed = subprocess.run(
            [sys.executable, str(parser), str(path), str(output), '--include-metadata'],
            capture_output=True, text=True,
        )
        if completed.returncode:
            # Do not surface parser stderr containing private paths/chat contents.
            raise RuntimeError('Transcript parser process failed')
        return json.loads(output.read_text(encoding='utf-8'))


def _files(connection, job_id):
    return connection.execute(
        'SELECT * FROM import_files WHERE job_id=? ORDER BY id', (job_id,)
    ).fetchall()


def _verify_originals(connection, job_id, root, chunk_bytes):
    """Check copied originals independently of the source folder."""
    records = _files(connection, job_id)
    if not records:
        raise ValueError('Archive has no inventoried files')
    for record in records:
        if record['state'] != 'verified':
            raise ValueError('Archive still has unverified files')
        path = _relative(root, record['stored_relative_path'])
        if _hash_file(path, chunk_bytes) != (record['expected_size'], record['source_sha256']):
            raise OSError('Archived original no longer matches its verified hash')


def _index(connection, metadata, root):
    """Commit messages, assets, reports and the indexed checkpoint together.

    A parser/write failure rolls back the complete index. Verified copies stay
    intact, and retry never appends a second copy of an already committed index.
    """
    inventory = json.loads(metadata['inventory_json'])
    records = {row['source_relative_path']: row for row in _files(connection, metadata['job_id'])}
    warnings = list(inventory['warnings'])
    reports = []
    with connection:
        cursor = connection.execute(
            'INSERT INTO imports(schema_version,source_root_path,imported_at,message_count,'
            'asset_count,metadata_json) VALUES (?,?,?,?,?,?)',
            (SCHEMA_VERSION, 'originals', _now(), 0, 0, '{}')
        )
        import_id = cursor.lastrowid
        message_count = 0
        asset_count = 0
        for item in inventory['files']:
            record = records[item['source_relative_path']]
            stored = record['stored_relative_path']
            if item['role'] == 'transcript':
                result = _parse_transcript(_relative(root, stored))
                status = result.get('parse_status')
                if status not in ('success', 'partial', 'unknown_format'):
                    raise ValueError('Recognized transcript could not be parsed')
                if status != 'success':
                    warnings.append(f'transcript_{status}: {item["source_relative_path"]}')
                reports.append({key: result.get(key) for key in (
                    'parse_status', 'detected_format', 'locale_guess', 'confidence',
                    'parser_used', 'warnings', 'reason'
                )} | {'source_file': stored})
                messages = []
                for index, message in enumerate(result['messages'], 1):
                    # Local IDs repeat in each transcript; preserve both identities.
                    local_id = message.get('localId') or f'msg_{index:06d}'
                    extra = dict(message.get('metadata') or {})
                    extra.update({key: message.get(key) for key in (
                        'raw', 'missing_fields', 'time_of_day', 'season'
                    )})
                    extra['sourceLocalId'] = local_id
                    messages.append({**message, 'localId': f'file_{record["id"]}:{local_id}',
                                     'source_file': stored, 'metadata': extra})
                _insert_messages(connection, import_id, messages)
                message_count += len(messages)
            else:
                name = Path(item['source_relative_path']).name
                extension = Path(name).suffix.lower()
                timestamp, sequence = _filename_metadata(name)
                _insert_assets(connection, import_id, [{
                    'local_id': f'asset_{record["id"]:06d}', 'original_filename': name,
                    'original_relative_path': item['source_relative_path'],
                    'asset_type': _asset_type(extension),
                    'mime_type': mimetypes.guess_type(name)[0], 'extension': extension,
                    'size_bytes': record['actual_size'], 'filename_timestamp': timestamp,
                    'filename_sequence': sequence,
                    'metadata_json': _json({'storedRelativePath': stored,
                                            'sha256': record['source_sha256'],
                                            'matchStatus': 'unmatched'}),
                }])
                asset_count += 1
        if not reports:
            warnings.append('no_recognized_transcripts')
        connection.execute(
            'UPDATE imports SET message_count=?,asset_count=?,metadata_json=? WHERE id=?',
            (message_count, asset_count, _json({'warnings': warnings, 'parserReports': reports}), import_id)
        )
        connection.execute(
            'UPDATE managed_archive SET indexed_import_id=?,indexed_at=? WHERE singleton=1',
            (import_id, _now())
        )


def _manifest(connection):
    metadata = connection.execute('SELECT * FROM managed_archive WHERE singleton=1').fetchone()
    if metadata is None or metadata['indexed_import_id'] is None:
        raise ValueError('Archive index is not ready')
    imported = connection.execute('SELECT * FROM imports WHERE id=?',
                                  (metadata['indexed_import_id'],)).fetchone()
    detail = json.loads(imported['metadata_json'])
    inventory = json.loads(metadata['inventory_json'])
    payload = {
        'schemaVersion': SCHEMA_VERSION, 'archiveId': metadata['archive_id'],
        'jobId': metadata['job_id'], 'state': 'ready', 'sourceCopied': True,
        'database': 'archive.sqlite', 'originals': 'originals',
        'indexedAt': metadata['indexed_at'], 'messageCount': imported['message_count'],
        'assetCount': imported['asset_count'],
        'fileCount': len(_files(connection, metadata['job_id'])),
        'formatIds': sorted({report['detected_format'] for report in detail['parserReports']}),
        'warnings': list(dict.fromkeys(detail['warnings'] + inventory['warnings'])),
    }
    # Older completed v3 archives lack this field; keep their manifest unchanged.
    if 'storageCapabilities' in inventory:
        payload['storageCapabilities'] = inventory['storageCapabilities']
    return payload


def _publish_manifest(root, payload):
    """Publish before DB completion; a retry accepts only the identical manifest."""
    target = root / 'manifest.json'
    fd, name = tempfile.mkstemp(prefix='.manifest-', suffix='.tmp', dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            publish_no_replace(temporary, target, recovery_root=root)
        except FileExistsError:
            with _reader(target) as stream:
                if json.load(stream) != payload:
                    raise ValueError('Existing manifest does not match this archive')
        _sync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def import_managed_archive(source, destination, *, reserve_bytes=DEFAULT_RESERVE_BYTES,
                           chunk_bytes=DEFAULT_CHUNK_BYTES) -> dict:
    """Create or resume one archive directory, returning its completed manifest.

    Pass source=None to resume from the recorded source path. Completed archives
    reopen from stored files only. Incomplete copies still require the source.
    """
    root = Path(destination).expanduser().resolve()
    # Validate overlap before even creating a destination lock file.
    if source is not None:
        candidate = Path(source).expanduser().resolve()
        if candidate == root or candidate in root.parents or root in candidate.parents:
            raise ValueError('Source and destination must not overlap')
    if not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
        raise ValueError('chunk_bytes must be positive')
    with _writer_lock(root):
        publication_warnings = recover_publications(root)
        database = root / 'archive.sqlite'
        if not database.exists():
            if source is None:
                raise ValueError('A source folder is required for a new archive')
            _bootstrap(source, root, reserve_bytes)
        else:
            # Managed archives first appear in v3. Do not migrate an unrelated
            # source-only database merely because it has the expected filename.
            with closing(_connect(database, readonly=True)) as existing:
                version = existing.execute('PRAGMA user_version').fetchone()[0]
                tables = {row[0] for row in existing.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if version != SCHEMA_VERSION or 'managed_archive' not in tables:
                    raise ValueError('Destination is not a supported managed archive')
                if existing.execute('SELECT 1 FROM managed_archive WHERE singleton=1').fetchone() is None:
                    raise ValueError('Destination has no managed archive identity')
        with closing(_connect(database)) as connection:
            journal = ImportJournal(connection)
            metadata = connection.execute('SELECT * FROM managed_archive WHERE singleton=1').fetchone()
            if metadata is None:
                raise ValueError('Database is not a managed archive')
            job_id = metadata['job_id']
            job = connection.execute('SELECT * FROM import_jobs WHERE id=?', (job_id,)).fetchone()
            if source is not None and str(candidate) != job['source_root']:
                raise ValueError('Source does not match the recorded import')
            if job['state'] == 'completed':
                return inspect_archive(root, verify_files=True)
            if str(root) != job['destination_root']:
                raise ValueError('An unfinished archive must resume at its original destination')
            try:
                capabilities = probe_storage(root)
                inventory = json.loads(metadata['inventory_json'])
                inventory['warnings'] = list(dict.fromkeys(inventory['warnings'] + publication_warnings))
                inventory['storageCapabilities'] = capabilities
                if not capabilities['directorySync']:
                    warning = 'directory_sync_unavailable_power_loss_durability_limited'
                    if warning not in inventory['warnings']:
                        inventory['warnings'].append(warning)
                with connection:
                    connection.execute('UPDATE managed_archive SET inventory_json=? WHERE singleton=1',
                                       (_json(inventory),))
                metadata = connection.execute('SELECT * FROM managed_archive WHERE singleton=1').fetchone()
                _copy_import(journal, job_id, chunk_bytes=chunk_bytes, acquire_lock=False)
                if metadata['indexed_import_id'] is None:
                    _index(connection, metadata, root)
                _verify_originals(connection, job_id, root, chunk_bytes)
                payload = _manifest(connection)
                _publish_manifest(root, payload)
                journal.transition_job(job_id, 'completed')
                return payload
            except Exception as exc:
                # Copying may already have recorded failure. Preserve its state.
                state = connection.execute('SELECT state FROM import_jobs WHERE id=?', (job_id,)).fetchone()[0]
                if state == 'running':
                    journal.transition_job(job_id, 'failed', error=type(exc).__name__)
                raise


def inspect_archive(destination, *, verify_files=False) -> dict:
    """Read a ready archive after restart/relocation without its source export.

    SQLite completion AND a matching manifest are required. Optional full hashing
    is useful for integrity checks; ordinary inspection checks sizes/existence.
    """
    root = Path(destination).expanduser().resolve(strict=True)
    with closing(_connect(root / 'archive.sqlite', readonly=True)) as connection:
        if connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unsupported managed archive version')
        expected = _manifest(connection)
        job = connection.execute('SELECT state FROM import_jobs WHERE id=?', (expected['jobId'],)).fetchone()
        if job is None or job['state'] != 'completed':
            raise ValueError('Archive import is incomplete')
        with _reader(root / 'manifest.json') as stream:
            if json.load(stream) != expected:
                raise ValueError('Archive manifest does not match database')
        if verify_files:
            _verify_originals(connection, expected['jobId'], root, DEFAULT_CHUNK_BYTES)
        else:
            for record in _files(connection, expected['jobId']):
                with _reader(_relative(root, record['stored_relative_path'])) as stream:
                    if record['state'] != 'verified' or os.fstat(stream.fileno()).st_size != record['expected_size']:
                        raise ValueError('Archived file is missing or incomplete')
        return expected
