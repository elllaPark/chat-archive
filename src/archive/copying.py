"""Verified, resumable file copying for a prepared import journal (POSIX MVP)."""

import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .database import ImportJournal
from .storage import _check_roots


DEFAULT_CHUNK_BYTES = 1024 * 1024


def _directory(path: Path) -> None:
    """Create directories without accepting symlinks in the destination path."""
    for part in reversed((path, *path.parents[:-1])):
        try:
            part.mkdir()
        except FileExistsError:
            pass
        if not stat.S_ISDIR(part.lstat().st_mode):
            raise ValueError('Destination path contains a symlink or non-directory')


def _relative(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError('Unsafe relative path in import journal')
    result = root / path
    for parent in result.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Symlink in import path')
    return result


@contextmanager
def _reader(path: Path):
    # O_NONBLOCK avoids hanging if a regular source was replaced with a FIFO.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Expected a regular file')
        yield stream


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


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_one(source: Path, target: Path, record: dict, chunk_bytes: int):
    """Publish verified bytes without replacing any existing destination file.

    Hard-link publication is atomic and refuses an existing name. Filesystems
    without hard links fail rather than falling back to an overwriting rename.
    Temporary files are on the destination volume; only our own temp is removed.
    """
    temporary = None
    try:
        with _reader(source) as stream:
            before = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (
                record['expected_size'], record['source_mtime_ns']
            ):
                raise OSError('Source snapshot changed since inventory')
            _directory(target.parent)
            fd, name = tempfile.mkstemp(prefix='.chat-import-', suffix='.part', dir=target.parent)
            temporary = Path(name)
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(fd, 'wb') as output:
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
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            # A previous run may have published the file before its DB commit.
            # Also tolerate identical existing bytes; different bytes are retained.
            if _hash_file(target, chunk_bytes) != (size, source_hash):
                raise FileExistsError('Destination contains different bytes')
        with _reader(target) as published:
            os.fsync(published.fileno())
        _sync_directory(target.parent)
        return size, source_hash
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _writer_lock(root: Path):
    import fcntl
    _directory(root)
    fd = os.open(root / '.import.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def copy_import(journal: ImportJournal, job_id: str, *, chunk_bytes=DEFAULT_CHUNK_BYTES) -> int:
    """Copy a prepared job and return verified file count, leaving job running.

    A failed job is restarted at file boundaries. Existing verified files are
    rehashed, never trusted solely because their journal status says verified.
    Job completion/manifest publication belongs to the later orchestrator.
    """
    if os.name != 'posix':
        raise NotImplementedError('Managed copying currently requires POSIX locks and hard links')
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
    with _writer_lock(destination):
        if job['state'] in ('paused', 'failed'):
            journal.transition_job(job_id, 'running')
        cursor = connection.execute('SELECT * FROM import_files WHERE job_id=? ORDER BY id', (job_id,))
        columns = [item[0] for item in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if not records:
            raise ValueError('No files registered for copying')
        verified = 0
        for record in records:
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
