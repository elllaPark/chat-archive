import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from archive.database import ImportJournal
from archive.importer import SCHEMA_SQL
from archive.storage import build_inventory, prepare_import, _allocate_component, _portable_component


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        self.target = self.base / 'archive'
        self.fixture = Path(__file__).parent / 'fixtures/parser/kakao_mobile_txt_ko_sample.txt'

    def test_inventory_classifies_and_preserves_sources(self):
        (self.source / 'chat.txt').write_bytes(self.fixture.read_bytes())
        (self.source / 'nested').mkdir()
        (self.source / 'nested/notes.txt').write_text('shared note')
        (self.source / 'photo.jpg').write_bytes(b'photo')
        (self.source / 'unknown.bin').write_bytes(b'unknown')
        before = {p.relative_to(self.source): p.read_bytes()
                  for p in self.source.rglob('*') if p.is_file()}
        inventory = build_inventory(self.source, self.target)
        self.assertEqual([f.role for f in inventory.files],
                         ['transcript', 'attachment', 'attachment', 'attachment'])
        self.assertEqual(inventory.total_bytes, sum(map(len, before.values())))
        self.assertFalse(self.target.exists())
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes()
                                 for p in self.source.rglob('*') if p.is_file()})
        self.assertTrue(inventory.warnings)

    def test_overlap_and_alias_rejected(self):
        for target in (self.source, self.source / 'archive', self.base):
            with self.assertRaises(ValueError):
                build_inventory(self.source, target)
        alias = self.base / 'alias'
        try:
            alias.symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation is unavailable')
        with self.assertRaises(ValueError):
            build_inventory(self.source, alias)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO requires POSIX')
    def test_skips_links_and_special_files(self):
        (self.source / 'regular').write_bytes(b'a')
        (self.source / 'link').symlink_to(self.base)
        os.mkfifo(self.source / 'pipe')
        inventory = build_inventory(self.source, self.target)
        self.assertEqual(len(inventory.files), 1)
        self.assertEqual(len(inventory.warnings), 2)

    def test_space_failure_does_not_create_destination(self):
        (self.source / 'file').write_bytes(b'123')
        with patch('archive.storage.shutil.disk_usage') as usage:
            usage.return_value.free = 3
            with self.assertRaises(OSError):
                build_inventory(self.source, self.target, reserve_bytes=1)
        self.assertFalse(self.target.exists())

    def test_empty_source_and_file_destination_rejected(self):
        with self.assertRaises(ValueError):
            build_inventory(self.source, self.target)
        self.target.write_bytes(b'not a directory')
        with self.assertRaises(NotADirectoryError):
            build_inventory(self.source, self.target / 'child')

    def test_bounded_sample_and_encoding_fallback(self):
        text = self.fixture.read_text(encoding='utf-8')
        (self.source / 'chat.txt').write_bytes(text.encode('cp949') + b'x' * 100000)
        inventory = build_inventory(self.source, self.target)
        self.assertEqual(inventory.files[0].role, 'transcript')

    def test_atomic_registration_and_duplicate_job(self):
        (self.source / 'a').write_bytes(b'123')
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.executescript(SCHEMA_SQL)
        journal = ImportJournal(connection)
        inventory = prepare_import(journal, 'job', self.source, self.target)
        row = connection.execute('SELECT source_relative_path,expected_size,state FROM import_files').fetchone()
        self.assertEqual(row, ('a', 3, 'pending'))
        with self.assertRaises(sqlite3.IntegrityError):
            prepare_import(journal, 'job', self.source, self.target)
        self.assertEqual(connection.execute('SELECT count(*) FROM import_files').fetchone()[0], 1)
        # A later insert failure must also roll back the new job itself.
        with self.assertRaises(sqlite3.IntegrityError):
            journal.register_inventory('broken', str(self.source), str(self.target),
                                       inventory.files + inventory.files)
        self.assertIsNone(connection.execute("SELECT id FROM import_jobs WHERE id='broken'").fetchone())

    def test_portable_filename_collision_is_disambiguated(self):
        (self.source / 'A.txt').write_bytes(b'one')
        second = self.source / 'a.txt'
        if second.exists():
            self.skipTest('Filesystem does not allow case-distinct filenames')
        second.write_bytes(b'two')
        inventory = build_inventory(self.source, self.target)
        stored = [item.stored_relative_path.casefold() for item in inventory.files]
        self.assertEqual(len(set(stored)), 2)
        self.assertTrue(all(path.endswith('.txt') for path in stored))

    def test_portable_name_rules_and_collision_allocator(self):
        expected = {'Talk_15:10.txt': 'Talk_15%3A10.txt', 'CON.txt': '%43ON.txt',
                    'a. ': 'a%2E%20', 'a%3A.txt': 'a%253A.txt',
                    'photo?.jpg': 'photo%3F.jpg', 'LPT1': '%4CPT1'}
        for original, stored in expected.items():
            self.assertEqual(_portable_component(original), stored)
        used = set()
        names = [_allocate_component(name, name, used)
                 for name in ('A.txt', 'a.txt', 'é.txt', 'e\u0301.txt')]
        self.assertEqual(len(set(name.casefold() for name in names)), 4)
        self.assertTrue(all(name.endswith('.txt') for name in names))

    def test_source_change_during_detection_is_rejected(self):
        path = self.source / 'chat.txt'
        path.write_bytes(b'original')
        def change_file(_):
            path.write_bytes(b'changed file size')
            return 'attachment', None, None
        with patch('archive.storage._classify', side_effect=change_file):
            with self.assertRaisesRegex(OSError, 'changed'):
                build_inventory(self.source, self.target)
