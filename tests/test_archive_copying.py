import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from archive import copy_import, prepare_import
from archive.database import ImportJournal
from archive.importer import SCHEMA_SQL
from archive import copying


@unittest.skipUnless(os.name == 'posix', 'POSIX copy MVP')
class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / 'source'
        self.destination = root / 'archive'
        self.source.mkdir()
        (self.source / 'nested').mkdir()
        self.content = b'original bytes' * 1000
        (self.source / 'nested/photo.jpg').write_bytes(self.content)
        (self.source / 'empty.bin').write_bytes(b'')
        self.dbpath = root / 'journal.sqlite'
        self.connection = sqlite3.connect(self.dbpath)
        self.connection.executescript(SCHEMA_SQL)
        self.journal = ImportJournal(self.connection)
        prepare_import(self.journal, 'job', self.source, self.destination, reserve_bytes=0)

    def tearDown(self):
        self.connection.close()

    @property
    def target(self):
        return self.destination / 'originals/nested/photo.jpg'

    def states(self):
        return [row[0] for row in self.connection.execute('SELECT state FROM import_files ORDER BY id')]

    def test_verified_copy_and_recheck_preserve_bytes(self):
        self.assertEqual(copy_import(self.journal, 'job', chunk_bytes=13), 2)
        self.assertEqual(self.target.read_bytes(), self.content)
        self.assertEqual((self.source / 'nested/photo.jpg').read_bytes(), self.content)
        self.assertEqual(self.states(), ['verified', 'verified'])
        digest = self.connection.execute(
            "SELECT source_sha256 FROM import_files WHERE source_relative_path='nested/photo.jpg'"
        ).fetchone()[0]
        self.assertEqual(digest, hashlib.sha256(self.content).hexdigest())
        self.assertEqual(copy_import(self.journal, 'job'), 2)
        self.assertFalse(list(self.destination.rglob('*.part')))

    def test_existing_different_file_is_not_overwritten(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'keep existing')
        with self.assertRaises(FileExistsError):
            copy_import(self.journal, 'job')
        self.assertEqual(self.target.read_bytes(), b'keep existing')
        self.assertEqual(self.states(), ['verified', 'failed'])

    def test_existing_identical_file_is_recovered(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.content)
        self.assertEqual(copy_import(self.journal, 'job'), 2)

    def test_source_change_after_inventory_fails(self):
        (self.source / 'nested/photo.jpg').write_bytes(b'changed')
        with self.assertRaisesRegex(OSError, 'snapshot'):
            copy_import(self.journal, 'job')
        self.assertFalse(self.target.exists())

    def test_write_failure_then_reopen_and_retry(self):
        with patch('archive.copying.os.fsync', side_effect=OSError(28, 'disk full')):
            with self.assertRaises(OSError):
                copy_import(self.journal, 'job')
        self.assertFalse(list(self.destination.rglob('*.part')))
        self.connection.close()
        self.connection = sqlite3.connect(self.dbpath)
        self.journal = ImportJournal(self.connection)
        self.assertEqual(copy_import(self.journal, 'job'), 2)

    def test_hash_mismatch_is_not_published(self):
        original = copying._hash_file
        def corrupt(path, chunk_bytes):
            size, digest = original(path, chunk_bytes)
            return (size, '0' * 64) if path.suffix == '.part' else (size, digest)
        with patch('archive.copying._hash_file', side_effect=corrupt):
            with self.assertRaisesRegex(OSError, 'mismatch'):
                copy_import(self.journal, 'job')
        self.assertFalse((self.destination / 'originals/empty.bin').exists())

    def test_publication_before_journal_failure_recovers(self):
        original = self.journal.transition_file
        def fail_commit(file_id, state, **kwargs):
            if state == 'verified':
                raise sqlite3.OperationalError('simulated checkpoint failure')
            return original(file_id, state, **kwargs)
        with patch.object(self.journal, 'transition_file', side_effect=fail_commit):
            with self.assertRaises(sqlite3.OperationalError):
                copy_import(self.journal, 'job')
        self.assertTrue((self.destination / 'originals/empty.bin').exists())
        self.assertEqual(copy_import(self.journal, 'job'), 2)

    def test_previously_verified_corruption_is_detected(self):
        copy_import(self.journal, 'job')
        self.target.write_bytes(b'corruption')
        with self.assertRaisesRegex(OSError, 'verified'):
            copy_import(self.journal, 'job')
        self.assertEqual(self.target.read_bytes(), b'corruption')

    def test_symlink_destination_is_rejected(self):
        self.destination.mkdir()
        outside = self.destination.parent / 'outside'
        outside.mkdir()
        (self.destination / 'originals').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            copy_import(self.journal, 'job')
        self.assertEqual(list(outside.iterdir()), [])

    def test_active_writer_blocks_second_copy(self):
        with copying._writer_lock(self.destination.resolve()):
            with self.assertRaises(BlockingIOError):
                copy_import(self.journal, 'job')

    def test_unsupported_publication_fails_without_final_file(self):
        with patch('archive.copying.os.link', side_effect=OSError('hard links unavailable')):
            with self.assertRaises(OSError):
                copy_import(self.journal, 'job')
        self.assertFalse((self.destination / 'originals/empty.bin').exists())

    def test_source_replaced_by_symlink_is_rejected(self):
        path = self.source / 'empty.bin'
        path.unlink()
        path.symlink_to(self.source / 'nested/photo.jpg')
        with self.assertRaises(OSError):
            copy_import(self.journal, 'job')

    def test_source_changes_during_copy_are_rejected(self):
        original = copying.os.fsync
        def change_source(fd):
            (self.source / 'empty.bin').write_bytes(b'changed during copy')
            original(fd)
        with patch('archive.copying.os.fsync', side_effect=change_source):
            with self.assertRaisesRegex(OSError, 'changed during'):
                copy_import(self.journal, 'job')
        self.assertFalse((self.destination / 'originals/empty.bin').exists())

    def test_interrupted_job_preserves_completed_file_and_retries(self):
        original = copying._copy_one
        def interrupt(source, target, record, chunk_bytes):
            if record['source_relative_path'] == 'nested/photo.jpg':
                raise KeyboardInterrupt()
            return original(source, target, record, chunk_bytes)
        with patch('archive.copying._copy_one', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                copy_import(self.journal, 'job')
        self.assertEqual(self.states(), ['verified', 'copying'])
        self.assertEqual(copy_import(self.journal, 'job'), 2)
        self.assertEqual(self.states(), ['verified', 'verified'])
