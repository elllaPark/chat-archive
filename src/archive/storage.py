"""Read-only source inventory and destination preflight before managed copying."""

import codecs
import hashlib
import os
import re
import shutil
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from parser.detector import UNKNOWN_FORMAT, detect_format
from parser.models import PreprocessedInput
from .database import ImportJournal
from .filesystem import is_link


SAMPLE_BYTES = 64 * 1024
DEFAULT_RESERVE_BYTES = 64 * 1024 * 1024


def _portable_component(name: str) -> str:
    """Encode Windows-invalid characters without losing the original DB name.

    Percent itself is escaped so literal percent sequences cannot collide with
    encoded characters. Trailing spaces/dots and device names need special care.
    """
    end = len(name.rstrip(' .'))
    encoded = ''.join(
        f'%{ord(char):02X}' if char in '%<>:"/\\|?*' or ord(char) < 32
        or (index >= end and char in ' .') else char
        for index, char in enumerate(name)
    )
    device = name.split('.', 1)[0].rstrip(' ').upper()
    if device in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', device):
        encoded = f'%{ord(name[0]):02X}' + encoded[1:]
    return encoded


def _allocate_component(name: str, relative: str, used: set[str]) -> str:
    """Disambiguate case/Unicode collisions deterministically within one folder."""
    candidate = _portable_component(name)
    key = lambda value: unicodedata.normalize('NFC', value).casefold()
    if key(candidate) in used:
        digest = hashlib.sha256(relative.encode('utf-8', errors='surrogatepass')).hexdigest()
        original = candidate
        suffix = Path(original).suffix
        stem = original[:-len(suffix)] if suffix else original
        for width in range(12, 65, 4):
            candidate = stem + '~' + digest[:width] + suffix
            if key(candidate) not in used:
                break
        else:
            raise ValueError('Unable to allocate a distinct stored filename')
    used.add(key(candidate))
    return candidate


@dataclass(frozen=True)
class InventoryFile:
    source_relative_path: str
    stored_relative_path: str
    size_bytes: int
    mtime_ns: int
    role: str
    detected_format: str | None = None


@dataclass(frozen=True)
class ImportInventory:
    source_root: Path
    destination_root: Path
    files: tuple[InventoryFile, ...]
    warnings: tuple[str, ...]
    total_bytes: int
    reserve_bytes: int
    free_bytes: int


def _check_roots(source, destination) -> tuple[Path, Path]:
    """Resolve aliases before comparing; reject either direction of overlap."""
    root = Path(source).expanduser().resolve(strict=True)
    target = Path(destination).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if root == target or root in target.parents or target in root.parents:
        raise ValueError('Source and destination must not overlap')
    # Check every existing ancestor so a file cannot masquerade as a parent dir.
    for ancestor in (target, *target.parents):
        if ancestor.exists() and not ancestor.is_dir():
            raise NotADirectoryError(ancestor)
    return root, target


def _classify(path: Path) -> tuple[str, str | None, str | None]:
    """Use a bounded sample; unsupported text stays preserved as an attachment."""
    suffix = path.suffix.lower().lstrip('.')
    if suffix not in ('txt', 'csv'):
        return 'attachment', None, None
    # Incremental decoding tolerates a sample boundary inside a multibyte character
    # without silently replacing invalid bytes elsewhere in the sample.
    with path.open('rb') as stream:
        data = stream.read(SAMPLE_BYTES)
    text = None
    for encoding in ('utf-8-sig', 'utf-8', 'cp949', 'euc-kr'):
        try:
            text = codecs.getincrementaldecoder(encoding)().decode(data, final=False)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return 'attachment', UNKNOWN_FORMAT, 'undecodable_text'
    text = text.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff')
    detection = detect_format(PreprocessedInput(
        str(path), suffix, encoding, text, text, text.split('\n')[:30]
    ))
    # A single English token/header is too weak to call a shared file a transcript.
    if detection.detected_format != UNKNOWN_FORMAT and detection.confidence >= 0.8:
        return 'transcript', detection.detected_format, None
    return 'attachment', detection.detected_format, 'unrecognized_text_format'


def build_inventory(source, destination, *, reserve_bytes=DEFAULT_RESERVE_BYTES) -> ImportInventory:
    """Inspect files and space without creating the destination or changing sources.

    Any unreadable or changing regular file aborts the snapshot; symlinks and
    special files are skipped explicitly. The copy step must recheck everything
    because this preflight cannot prevent later changes or disk exhaustion.
    """
    if not isinstance(reserve_bytes, int) or reserve_bytes < 0:
        raise ValueError('reserve_bytes must be a nonnegative integer')
    root, target = _check_roots(source, destination)
    files = []
    warnings = []
    def visit(directory, stored_parent='originals'):
        used_names = set()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda item: item.name)
        for entry in ordered:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            snapshot = entry.stat(follow_symlinks=False)
            if is_link(snapshot):
                warnings.append(f'skipped_symlink: {relative}')
            elif stat.S_ISDIR(snapshot.st_mode):
                name = _allocate_component(entry.name, relative, used_names)
                if name != entry.name:
                    warnings.append(f'stored_name_encoded: {relative}')
                visit(path, stored_parent + '/' + name)
            elif stat.S_ISREG(snapshot.st_mode):
                name = _allocate_component(entry.name, relative, used_names)
                if name != entry.name:
                    warnings.append(f'stored_name_encoded: {relative}')
                role, format_id, warning = _classify(path)
                after = path.lstat()
                if (snapshot.st_dev, snapshot.st_ino, snapshot.st_size, snapshot.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
                ):
                    raise OSError(f'Source changed during inventory: {relative}')
                stored = stored_parent + '/' + name
                files.append(InventoryFile(relative, stored, snapshot.st_size,
                                           snapshot.st_mtime_ns, role, format_id))
                if warning:
                    warnings.append(f'{warning}: {relative}')
            else:
                warnings.append(f'skipped_special_file: {relative}')

    visit(root)
    files.sort(key=lambda item: item.source_relative_path)
    if not files:
        raise ValueError('No regular files to import')
    total = sum(item.size_bytes for item in files)
    ancestor = target
    while not ancestor.exists():
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    if free < total + reserve_bytes:
        raise OSError(28, 'Insufficient destination space for files and reserve')
    return ImportInventory(root, target, tuple(files), tuple(warnings), total, reserve_bytes, free)


def prepare_import(journal: ImportJournal, job_id: str, source, destination,
                   *, reserve_bytes=DEFAULT_RESERVE_BYTES) -> ImportInventory:
    """Persist a checked inventory in one transaction, without starting copies."""
    inventory = build_inventory(source, destination, reserve_bytes=reserve_bytes)
    journal.register_inventory(job_id, str(inventory.source_root),
                               str(inventory.destination_root), inventory.files)
    return inventory
