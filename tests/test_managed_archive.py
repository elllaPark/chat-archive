import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from archive import import_managed_archive, inspect_archive
from archive import managed
from archive.database import ImportJournal
from archive.importer import SCHEMA_SQL
from archive import filesystem as fs


class ManagedArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir()
        self.destination = self.base / 'archive'
        self.fixture = ROOT / 'tests/fixtures/parser/kakao_mobile_txt_ko_sample.txt'
        (self.source / 'chat.txt').write_bytes(self.fixture.read_bytes())
        (self.source / '20260525_120100_1.jpg').write_bytes(b'fake photo')
        (self.source / 'notes.txt').write_text('shared note', encoding='utf-8')

    def query(self, sql):
        with closing(sqlite3.connect(self.destination / 'archive.sqlite')) as connection:
            return connection.execute(sql).fetchall()

    def run_import(self):
        return import_managed_archive(self.source, self.destination, reserve_bytes=0)

    def test_complete_archive_reopens_without_source_and_after_move(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        result = self.run_import()
        self.assertEqual((result['messageCount'], result['assetCount'], result['fileCount']), (3, 2, 3))
        self.assertEqual(self.query('SELECT state FROM import_jobs'), [('completed',)])
        self.assertEqual(self.query('SELECT source_file FROM messages LIMIT 1'), [('originals/chat.txt',)])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        assets = self.query('SELECT original_relative_path,metadata_json FROM assets')
        for relative, metadata in assets:
            stored = json.loads(metadata)['storedRelativePath']
            self.assertEqual((self.destination / stored).read_bytes(), before[relative])
        shutil.rmtree(self.source)
        self.assertEqual(inspect_archive(self.destination, verify_files=True), result)
        moved = self.base / 'moved_archive'
        self.destination.rename(moved)
        self.assertEqual(inspect_archive(moved, verify_files=True), result)

    def test_repeat_call_has_no_duplicate_index(self):
        first = self.run_import()
        self.assertEqual(self.run_import(), first)
        self.assertEqual(self.query('SELECT count(*) FROM imports'), [(1,)])
        self.assertEqual(self.query('SELECT count(*) FROM messages'), [(3,)])

    def test_file_local_ids_are_namespaced_and_raw_metadata_preserved(self):
        (self.source / 'second.txt').write_bytes(self.fixture.read_bytes())
        result = self.run_import()
        self.assertEqual(result['messageCount'], 6)
        self.assertEqual(self.query('SELECT count(DISTINCT local_id) FROM messages'), [(6,)])
        metadata = json.loads(self.query('SELECT metadata_json FROM messages LIMIT 1')[0][0])
        self.assertIn('raw', metadata)
        self.assertIn('missing_fields', metadata)
        self.assertEqual(metadata['sourceLocalId'], 'msg_000001')

    def test_parser_failure_keeps_originals_and_retries_without_duplicates(self):
        with patch('archive.managed._parse_transcript', side_effect=RuntimeError('parser failed')):
            with self.assertRaises(RuntimeError):
                self.run_import()
        self.assertTrue((self.destination / 'originals/chat.txt').is_file())
        self.assertFalse((self.destination / 'manifest.json').exists())
        self.assertEqual(self.query('SELECT count(*) FROM imports'), [(0,)])
        self.assertEqual(self.query('SELECT state FROM import_jobs'), [('failed',)])
        result = import_managed_archive(None, self.destination)
        self.assertEqual(result['messageCount'], 3)
        self.assertEqual(self.query('SELECT count(*) FROM imports'), [(1,)])

    def test_index_insert_failure_rolls_back_all_tables(self):
        with patch('archive.managed._insert_assets', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_import()
        self.assertEqual(self.query('SELECT count(*) FROM messages'), [(0,)])
        self.assertEqual(self.query('SELECT indexed_import_id FROM managed_archive'), [(None,)])
        self.assertEqual(self.run_import()['assetCount'], 2)

    def test_manifest_failure_reuses_committed_index(self):
        with patch('archive.managed._publish_manifest', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.run_import()
        self.assertEqual(self.query('SELECT count(*) FROM imports'), [(1,)])
        with patch('archive.managed._parse_transcript', side_effect=AssertionError('must not reparse')):
            self.assertEqual(self.run_import()['messageCount'], 3)

    def test_manifest_published_before_completion_commit_is_reconciled(self):
        original = ImportJournal.transition_job
        def fail_completion(journal, job_id, state, **kwargs):
            if state == 'completed':
                raise sqlite3.OperationalError('completion checkpoint interrupted')
            return original(journal, job_id, state, **kwargs)
        with patch.object(ImportJournal, 'transition_job', fail_completion):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_import()
        self.assertTrue((self.destination / 'manifest.json').exists())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            inspect_archive(self.destination)
        self.assertEqual(self.run_import()['messageCount'], 3)
        self.assertEqual(self.query('SELECT count(*) FROM imports'), [(1,)])

    def test_corrupted_original_prevents_open(self):
        self.run_import()
        (self.destination / 'originals/20260525_120100_1.jpg').write_bytes(b'corruption')
        with self.assertRaises((ValueError, OSError)):
            inspect_archive(self.destination, verify_files=True)

    def test_unrelated_destination_is_not_overwritten(self):
        self.destination.mkdir()
        (self.destination / 'manifest.json').write_text('keep me')
        with self.assertRaises(FileExistsError):
            self.run_import()
        self.assertEqual((self.destination / 'manifest.json').read_text(), 'keep me')
        self.assertFalse((self.destination / 'archive.sqlite').exists())

    def test_source_overlap_rejected_before_writes(self):
        with self.assertRaises(ValueError):
            import_managed_archive(self.source, self.source / 'archive')
        self.assertFalse((self.source / 'archive').exists())

    def test_unknown_transcript_preserved_with_warning(self):
        (self.source / 'chat.txt').write_text('unrecognized export convention')
        result = self.run_import()
        self.assertEqual(result['messageCount'], 0)
        self.assertIn('no_recognized_transcripts', result['warnings'])
        self.assertTrue((self.destination / 'originals/chat.txt').is_file())

    def test_partial_csv_system_rows_are_retained(self):
        (self.source / 'chat.txt').unlink()
        csv = ROOT / 'tests/fixtures/parser/kakao_pc_csv_ko_sample.csv'
        (self.source / 'chat.csv').write_bytes(csv.read_bytes())
        result = self.run_import()
        self.assertEqual(result['messageCount'], 5)
        self.assertEqual(self.query("SELECT count(*) FROM messages WHERE type='system' AND sender IS NULL"), [(1,)])
        self.assertTrue(any('partial' in warning for warning in result['warnings']))

    def test_cli_import_and_inspect(self):
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
        created = subprocess.run([sys.executable, '-m', 'archive', 'import', str(self.source),
                                  str(self.destination)], env=env, capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        shutil.rmtree(self.source)
        opened = subprocess.run([sys.executable, '-m', 'archive', 'inspect', str(self.destination),
                                 '--verify-files'], env=env, capture_output=True, text=True)
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertEqual(json.loads(created.stdout), json.loads(opened.stdout))

    def test_unrelated_database_is_not_migrated(self):
        self.destination.mkdir()
        with closing(sqlite3.connect(self.destination / 'archive.sqlite')) as connection:
            connection.executescript(SCHEMA_SQL)
        with self.assertRaisesRegex(ValueError, 'not a supported'):
            self.run_import()
        self.assertEqual(self.query('PRAGMA user_version'), [(0,)])
        self.assertEqual(self.query("SELECT count(*) FROM sqlite_master WHERE name='managed_archive'"), [(0,)])

    def test_changed_manifest_is_not_overwritten(self):
        self.run_import()
        path = self.destination / 'manifest.json'
        altered = json.loads(path.read_text())
        altered['messageCount'] = 999
        path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.run_import()
        self.assertEqual(json.loads(path.read_text())['messageCount'], 999)

    def test_full_import_without_exclusive_rename_or_hardlinks(self):
        unsupported = fs.UnsupportedStorageError(95, 'no exclusive rename')
        with patch.object(fs, '_native_publish', side_effect=unsupported), \
                patch.object(os, 'link', side_effect=AssertionError('hard links forbidden')):
            result = self.run_import()
        self.assertEqual(result['messageCount'], 3)
        self.assertEqual(result['storageCapabilities']['publicationMethod'], 'exclusive_create_verified')
        shutil.rmtree(self.source)
        self.assertEqual(inspect_archive(self.destination, verify_files=True), result)

    def test_missing_directory_sync_is_recorded(self):
        with patch.object(fs, 'sync_directory', return_value=False):
            result = self.run_import()
        self.assertFalse(result['storageCapabilities']['directorySync'])
        self.assertIn('directory_sync_unavailable_power_loss_durability_limited', result['warnings'])

    @unittest.skipIf(os.name == 'nt', 'Windows cannot create colon filenames')
    def test_windows_invalid_transcript_name_is_stored_portably(self):
        (self.source / 'chat.txt').rename(self.source / 'Talk_15:10.txt')
        result = self.run_import()
        self.assertEqual(result['messageCount'], 3)
        self.assertTrue((self.destination / 'originals/Talk_15%3A10.txt').exists())
        self.assertEqual(self.query('SELECT source_file FROM messages LIMIT 1'), [('originals/Talk_15%3A10.txt',)])

    def test_interrupted_fallback_database_and_manifest_are_recovered(self):
        for index, interrupted_name in enumerate(('archive.sqlite', 'manifest.json')):
            with self.subTest(interrupted_name=interrupted_name):
                self.destination = self.base / f'archive_{index}'
                original = fs._finish_publication
                def interrupt(folder, owner):
                    if owner['target'] == interrupted_name:
                        target = folder.parent / owner['target']
                        with target.open('xb') as output:
                            output.write((folder / 'payload').read_bytes()[:32])
                            output.flush()
                            owner['identity'] = fs._identity(os.fstat(output.fileno()))
                            fs._write_owner(folder, owner)
                        raise OSError('interrupted publication')
                    return original(folder, owner)
                unavailable = fs.UnsupportedStorageError('exclusive rename unavailable')
                with patch.object(fs, '_native_publish', side_effect=unavailable):
                    with patch.object(fs, '_finish_publication', side_effect=interrupt):
                        with self.assertRaises(OSError):
                            self.run_import()
                    result = self.run_import()
                self.assertEqual(result['messageCount'], 3)
                self.assertEqual(self.query('SELECT count(*) FROM imports'), [(1,)])
                self.assertEqual(inspect_archive(self.destination, verify_files=True), result)

    def test_control_like_directories_in_originals_are_only_data(self):
        data = self.source / '.chat-publish-example'
        data.mkdir()
        (data / 'owner.json').write_text('{"kind":"exclusive_create_publication","version":1}')
        (data / 'payload').write_bytes(b'keep this attachment')
        self.run_import()
        self.assertEqual((self.destination / 'originals/.chat-publish-example/payload').read_bytes(),
                         b'keep this attachment')
        self.run_import()
