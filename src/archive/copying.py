"""Verified, resumable file copying for a prepared import journal."""

import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path

from .database import ImportJournal
from .storage import _check_roots
from .filesystem import (ensure_directory as _directory, reader as _reader,
                         sync_directory as _sync_directory, writer_lock as _writer_lock,
                         publish_no_replace, sync_file, owned_stage, recover_staging,
                         recover_publications, is_link)


DEFAULT_CHUNK_BYTES = 1024 * 1024


def _relative(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError('Unsafe relative path in import journal')
    result = root / path
    for parent in result.parents:
        if parent == root:
            break
        if parent.exists() and is_link(parent.lstat()):
            raise ValueError('Symlink in import path')
    return result


def _snapshot(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _hash_file(path: Path, chunk_bytes: int) -> tuple[int, str]:
    """Reopen copied bytes independently; do not trust the write buffer's hash."""
    digest = hashlib.sha256()
    size = 0
    with _reader(path) as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(chunk_bytes):
            size += len(chunk)
            digest.update(chunk)
        if _snapshot(before) != _snapshot(os.fstat(stream.fileno())):
            raise OSError('File changed during hashing')
        if _snapshot(before) != _snapshot(path.lstat()):
            raise OSError('File replaced during hashing')
    return size, digest.hexdigest()


def _copy_one(source: Path, target: Path, record: dict, chunk_bytes: int):
    """Publish verified bytes without replacing any existing destination file.

    Exclusive rename refuses an existing name without requiring hard links.
    A marked staging workspace on the destination volume supports safe cleanup.
    """
    with owned_stage(Path(record['destination_root']), record['job_id'], record) as temporary:
        with _reader(source) as stream:
            before = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (
                record['expected_size'], record['source_mtime_ns']
            ):
                raise OSError('Source snapshot changed since inventory')
            _directory(target.parent)
            digest = hashlib.sha256()
            size = 0
            with temporary.open('xb') as output:
                while chunk := stream.read(chunk_bytes):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if _snapshot(before) != _snapshot(os.fstat(stream.fileno())):
                raise OSError('Source changed during copying')
            if _snapshot(before) != _snapshot(source.lstat()):
                raise OSError('Source replaced during copying')
        source_hash = digest.hexdigest()
        if _hash_file(temporary, chunk_bytes) != (size, source_hash) or size != record['expected_size']:
            raise OSError('Copied file size or SHA-256 mismatch')
        try:
            publish_no_replace(temporary, target, recovery_root=Path(record['destination_root']))
        except FileExistsError:
            # A previous run may have published the file before its DB commit.
            # Also tolerate identical existing bytes; different bytes are retained.
            if _hash_file(target, chunk_bytes) != (size, source_hash):
                raise FileExistsError('Destination contains different bytes')
        sync_file(target)
        _sync_directory(target.parent)
        return size, source_hash


def copy_import(journal: ImportJournal, job_id: str, *, chunk_bytes=DEFAULT_CHUNK_BYTES) -> int:
    """Copy a prepared job and return verified file count, leaving job running.

    A failed job is restarted at file boundaries. Existing verified files are
    rehashed, never trusted solely because their journal status says verified.
    Job completion/manifest publication belongs to the later orchestrator.
    """
    return _copy_import(journal, job_id, chunk_bytes=chunk_bytes, acquire_lock=True)


def _copy_import(journal, job_id, *, chunk_bytes, acquire_lock):
    """Internal entry point when orchestration already holds the destination lock."""
    if not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
        raise ValueError('chunk_bytes must be a positive integer')
    connection = journal.connection
    cursor = connection.execute('SELECT * FROM import_jobs WHERE id=?', (job_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError('Unknown import job')
    job = dict(zip((item[0] for item in cursor.description), row))
    if job['state'] == 'completed':
        raise ValueError('Job already completed')
    source, destination = _check_roots(job['source_root'], job['destination_root'])
    if str(source) != job['source_root'] or str(destination) != job['destination_root']:
        raise ValueError('Import roots changed since inventory')
    # The journal itself must never be copied as source data.
    for _, _, filename in connection.execute('PRAGMA database_list'):
        if filename and Path(filename).resolve().is_relative_to(source):
            raise ValueError('Import journal must be outside source root')
    with _writer_lock(destination) if acquire_lock else nullcontext():
        if job['state'] in ('paused', 'failed'):
            journal.transition_job(job_id, 'running')
        cursor = connection.execute('SELECT * FROM import_files WHERE job_id=? ORDER BY id', (job_id,))
        columns = [item[0] for item in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if not records:
            raise ValueError('No files registered for copying')
        warnings = recover_publications(destination) + recover_staging(destination, job_id, records)
        if warnings:
            managed = connection.execute('SELECT inventory_json FROM managed_archive WHERE job_id=?', (job_id,)).fetchone()
            if managed:
                report = json.loads(managed[0])
                report['warnings'] = list(dict.fromkeys(report['warnings'] + warnings))
                with connection:
                    connection.execute('UPDATE managed_archive SET inventory_json=? WHERE job_id=?',
                                       (json.dumps(report, ensure_ascii=False), job_id))
        verified = 0
        for record in records:
            record['destination_root'] = str(destination)
            try:
                # A crashed process can leave copying state behind.
                if record['state'] == 'copying':
                    journal.transition_file(record['id'], 'failed', error='Interrupted copy')
                journal.transition_file(record['id'], 'copying')
                source_path = _relative(source, record['source_relative_path'])
                target_path = _relative(destination, record['stored_relative_path'])
                if record['state'] == 'verified':
                    size, digest = _hash_file(source_path, chunk_bytes)
                    info = source_path.lstat()
                    if (size, info.st_mtime_ns, digest) != (
                        record['expected_size'], record['source_mtime_ns'], record['source_sha256']
                    ) or _hash_file(target_path, chunk_bytes) != (size, digest):
                        raise OSError('Previously verified file changed')
                else:
                    size, digest = _copy_one(source_path, target_path, record, chunk_bytes)
                journal.transition_file(record['id'], 'verified', actual_size=size,
                                        source_sha256=digest, destination_sha256=digest)
                verified += 1
            except Exception as exc:
                journal.transition_file(record['id'], 'failed', error=type(exc).__name__)
                journal.transition_job(job_id, 'failed', error=type(exc).__name__)
                raise
        return verified
