"""Small OS adapter for checked opens, exclusive publication and writer locks.

Publication always refuses existing destinations. Directory syncing is reported
separately because Windows and some removable filesystems cannot provide it.
"""

import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


_PLATFORM = sys.platform
_UNSUPPORTED = {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}


class UnsupportedStorageError(OSError):
    """The selected storage cannot safely execute a required primitive."""


def is_link(info) -> bool:
    """Include Windows junctions/reparse points, not just Unix symbolic links."""
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def ensure_directory(path: Path) -> None:
    for part in reversed((path, *path.parents[:-1])):
        try:
            part.mkdir()
        except FileExistsError:
            pass
        info = part.lstat()
        if is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Destination contains a link or non-directory')


def open_regular(path: Path, flags=os.O_RDONLY, mode=0o600) -> int:
    """Reject links before and after opening; the caller owns the returned fd.

    O_NOFOLLOW is used when available. Rechecking handles/path identity provides
    a portable guard, not a sandbox against hostile concurrent directory edits.
    """
    try:
        before = path.lstat()
    except FileNotFoundError:
        before = None
    if before is not None and (is_link(before) or not stat.S_ISREG(before.st_mode)):
        raise ValueError('Expected a regular file, not a link')
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    flags |= getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0)
    fd = os.open(path, flags, mode)
    try:
        opened, after = os.fstat(fd), path.lstat()
        if is_link(after) or not stat.S_ISREG(opened.st_mode):
            raise ValueError('Expected a regular file, not a link')
        for info in (before, after):
            if info is not None and info.st_ino and opened.st_ino and (
                info.st_dev, info.st_ino
            ) != (opened.st_dev, opened.st_ino):
                raise OSError('File changed while opening')
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def reader(path: Path):
    with os.fdopen(open_regular(path), 'rb') as stream:
        yield stream


def sync_file(path: Path) -> None:
    # Windows FlushFileBuffers needs a writable handle; opening read/write does
    # not modify the file and avoids fsync failures on read-only descriptors.
    fd = open_regular(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_directory(path: Path) -> bool:
    """Return False only for unavailable directory sync; actual I/O errors fail."""
    if _PLATFORM == 'win32':
        return False
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        try:
            os.fsync(fd)
            return True
        except OSError as exc:
            if exc.errno in _UNSUPPORTED:
                return False
            raise
    finally:
        os.close(fd)


def _posix_publish(source, target):
    library = ctypes.CDLL(None, use_errno=True)
    try:
        if _PLATFORM == 'darwin':
            rename = library.renamex_np
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            arguments = (os.fsencode(source), os.fsencode(target), 0x4)  # RENAME_EXCL
        elif _PLATFORM.startswith('linux'):
            rename = library.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                               ctypes.c_char_p, ctypes.c_uint]
            arguments = (-100, os.fsencode(source), -100, os.fsencode(target), 1)  # AT_FDCWD / RENAME_NOREPLACE
        else:
            raise AttributeError('No exclusive rename backend')
    except AttributeError as exc:
        raise UnsupportedStorageError(errno.ENOTSUP, 'Exclusive rename is unavailable') from exc
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        code = ctypes.get_errno()
        if code in _UNSUPPORTED:
            raise UnsupportedStorageError(code, 'Volume does not support exclusive rename')
        raise OSError(code, os.strerror(code), str(target))


def _native_publish(source, target):
    if _PLATFORM == 'win32':
        # Python documents that Windows rename always fails when dst exists.
        os.rename(source, target)
    else:
        _posix_publish(source, target)


def publish_no_replace(source: Path, target: Path, *, recovery_root=None) -> str:
    """Publish without replacement, retaining recovery evidence on fallback volumes."""
    if is_link(source.lstat()) or not source.is_file():
        raise ValueError('Publication requires a regular staging file')
    try:
        _native_publish(source, target)
    except UnsupportedStorageError:
        _fallback_publish(source, target, recovery_root=recovery_root)
        return 'exclusive_create_verified'
    return 'exclusive_rename'


def _digest(path):
    digest = hashlib.sha256()
    size = 0
    with reader(path) as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('Publication payload changed while hashing')
    return size, digest.hexdigest()


def _write_owner(folder, owner):
    fd, name = tempfile.mkstemp(prefix='.owner-', dir=folder)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(owner, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Only this private workspace's metadata is replaced, never archive data.
        os.replace(temporary, folder / 'owner.json')
        sync_directory(folder)
    finally:
        temporary.unlink(missing_ok=True)


def _identity(info):
    return {'device': info.st_dev, 'inode': info.st_ino}


def _finish_publication(folder, owner):
    """Recover only an app-created destination whose bytes match payload's prefix."""
    payload = folder / 'payload'
    name = owner.get('target')
    if not isinstance(name, str):
        raise ValueError('Invalid publication target')
    relative = Path(name)
    if relative.is_absolute() or relative.drive or not relative.parts or '..' in relative.parts:
        raise ValueError('Invalid publication target')
    target = folder.parent / relative
    ensure_directory(target.parent)
    expected = (owner['size'], owner['sha256'])
    if _digest(payload) != expected:
        raise ValueError('Publication recovery payload is not intact')
    if os.path.lexists(target) and _digest(target) == expected:
        sync_file(target)
        sync_directory(target.parent)
        return
    if os.path.lexists(target):
        info = target.lstat()
        if not info.st_ino or owner.get('identity') != _identity(info):
            raise FileExistsError('Existing destination ownership cannot be proven')
        fd = open_regular(target, os.O_RDWR)
    else:
        fd = open_regular(target, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        info = os.fstat(fd)
        owner['identity'] = _identity(info)
        try:
            _write_owner(folder, owner)
        except BaseException:
            os.close(fd)
            raise
    with os.fdopen(fd, 'r+b') as output, reader(payload) as source:
        info = os.fstat(output.fileno())
        if owner.get('identity') != _identity(info) or not info.st_ino:
            raise FileExistsError('Destination identity changed during recovery')
        # Never truncate or rewrite any existing bytes. Only a proven matching
        # prefix of the retained verified payload can be extended.
        remaining = info.st_size
        if remaining > owner['size']:
            raise FileExistsError('Destination is larger than the owned payload')
        while remaining:
            chunk = output.read(min(remaining, 1024 * 1024))
            if not chunk or chunk != source.read(len(chunk)):
                raise FileExistsError('Destination prefix changed; preserving conflict')
            remaining -= len(chunk)
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if _digest(target) != expected:
        raise OSError('Published file failed size or SHA-256 verification')
    sync_directory(target.parent)


def _cleanup_publication(folder):
    # A crash may leave unknown files: keep them rather than use recursive deletion.
    if {entry.name for entry in folder.iterdir()} != {'owner.json', 'payload'}:
        return False
    (folder / 'payload').unlink()
    (folder / 'owner.json').unlink()
    folder.rmdir()
    return True


def _fallback_publish(source, target, *, recovery_root=None):
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, 'Destination already exists', str(target))
    size, digest = _digest(source)
    root = Path(recovery_root) if recovery_root is not None else target.parent
    relative = target.relative_to(root).as_posix()
    folder = Path(tempfile.mkdtemp(prefix='.chat-publish-', dir=root))
    # This name is inside a newly created private directory. Plain rename is
    # safe here; it is never used to publish over a user-visible destination.
    os.rename(source, folder / 'payload')
    owner = {'version': 1, 'kind': 'exclusive_create_publication', 'target': relative,
             'size': size, 'sha256': digest, 'identity': None}
    _write_owner(folder, owner)
    # Leave payload+owner on errors so restart can prove what it may continue.
    _finish_publication(folder, owner)
    _cleanup_publication(folder)


def recover_publications(root: Path) -> list[str]:
    """Recover marked publications, including a DB interrupted during bootstrap."""
    warnings = []
    # Only root-level control workspaces are ours. Never interpret directories
    # inside originals/ as instructions, even if their names resemble staging.
    for folder in sorted(root.glob('.chat-publish-*')):
        if is_link(folder.lstat()):
            warnings.append('unrecognized_publication_retained')
            continue
        try:
            with reader(folder / 'owner.json') as stream:
                owner = json.load(stream)
            if owner.get('kind') != 'exclusive_create_publication' or owner.get('version') != 1:
                raise ValueError('Unrecognized publication metadata')
        except (OSError, ValueError, AttributeError):
            warnings.append('unrecognized_publication_retained')
            continue
        _finish_publication(folder, owner)
        if not _cleanup_publication(folder):
            warnings.append('unrecognized_publication_retained')
    return list(dict.fromkeys(warnings))


@contextmanager
def writer_lock(root: Path):
    """One nonblocking writer per archive; OS closes release locks after crashes."""
    ensure_directory(root)
    fd = open_regular(root / '.import.lock', os.O_CREAT | os.O_RDWR)
    locked = False
    try:
        if _PLATFORM == 'win32':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise BlockingIOError(errno.EAGAIN, 'Archive writer is already active') from exc
                raise
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        try:
            if locked and _PLATFORM == 'win32':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


def probe_storage(root: Path) -> dict:
    """Exercise required primitives only inside a private disposable directory."""
    with tempfile.TemporaryDirectory(prefix='.storage-probe-', dir=root) as directory:
        folder = Path(directory)
        first, second, target = folder / 'first', folder / 'second', folder / 'target'
        first.write_bytes(b'first')
        second.write_bytes(b'second')
        sync_file(first)
        method = publish_no_replace(first, target)
        try:
            publish_no_replace(second, target)
        except FileExistsError:
            pass
        else:
            raise UnsupportedStorageError(errno.ENOTSUP, 'Volume did not refuse replacement')
        if target.read_bytes() != b'first' or second.read_bytes() != b'second':
            raise UnsupportedStorageError(errno.ENOTSUP, 'Publication probe changed existing bytes')
        directory_sync = sync_directory(folder)
    return {'exclusivePublication': True, 'directorySync': directory_sync,
            'writerLock': 'msvcrt' if _PLATFORM == 'win32' else 'flock',
            'publicationMethod': method}


def _owner(job_id, record):
    return {'version': 1, 'jobId': job_id, 'fileId': record['id'],
            'storedRelativePath': record['stored_relative_path']}


def _cleanup_stage(folder: Path, owner: dict) -> bool:
    """Remove only validated app-owned files; retain unknown entries for review."""
    if is_link(folder.lstat()):
        return False
    entries = {entry.name for entry in folder.iterdir()}
    if not entries <= {'owner.json', 'payload.part'} or 'owner.json' not in entries:
        return False
    try:
        with reader(folder / 'owner.json') as stream:
            if json.load(stream) != owner:
                return False
        if 'payload.part' in entries:
            with reader(folder / 'payload.part'):
                pass
    except (ValueError, OSError):
        return False
    (folder / 'payload.part').unlink(missing_ok=True)
    (folder / 'owner.json').unlink()
    folder.rmdir()
    return True


@contextmanager
def owned_stage(root: Path, job_id: str, record: dict):
    """Write an ownership marker before creating a payload so restart can clean it."""
    folder = Path(tempfile.mkdtemp(prefix='.chat-stage-', dir=root))
    owner = _owner(job_id, record)
    try:
        with (folder / 'owner.json').open('x', encoding='utf-8') as stream:
            json.dump(owner, stream)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(folder)
        yield folder / 'payload.part'
    finally:
        _cleanup_stage(folder, owner)


def recover_staging(root: Path, job_id: str, records) -> list[str]:
    """Under the writer lock, discard only marked partial copies for this job.

    Legacy random temporaries have no ownership proof and are retained. An
    interrupted file is copied again from its start after owned staging cleanup.
    """
    owners = {record['id']: _owner(job_id, record) for record in records}
    warnings = []
    for folder in sorted(root.glob('.chat-stage-*')):
        try:
            if is_link(folder.lstat()):
                warnings.append('unrecognized_staging_retained')
                continue
            with reader(folder / 'owner.json') as stream:
                owner = json.load(stream)
            expected = owners.get(owner.get('fileId'))
            if expected is None or owner != expected or not _cleanup_stage(folder, expected):
                warnings.append('unrecognized_staging_retained')
        except (OSError, ValueError, AttributeError, TypeError):
            warnings.append('unrecognized_staging_retained')
    return list(dict.fromkeys(warnings))
