import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from archive.database import ImportJournal, migrate
from archive.importer import SCHEMA_SQL


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA_SQL)
        self.db.execute("INSERT INTO imports VALUES (1,1,'source','date',0,0,'{}')")
        self.db.commit()
        self.journal = ImportJournal(self.db)
        self.journal.create_job('job', '/source', '/destination')
        self.file = self.journal.add_file('job', 'photo.jpg', 'originals/photo.jpg', 3, 42)

    def tearDown(self):
        self.db.close()

    def test_migration_is_repeatable_and_preserves_legacy_rows(self):
        migrate(self.db)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT schema_version FROM imports').fetchone()[0], 1)

    def test_rejects_future_version_without_changes(self):
        self.db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            migrate(self.db)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 99)

    def test_migrates_version_two_without_changing_progress(self):
        self.journal.transition_file(self.file, 'copying')
        self.journal.transition_job('job', 'paused')
        self.db.execute('DROP TABLE managed_archive')
        self.db.execute('PRAGMA user_version=2')
        migrate(self.db)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT state FROM import_jobs').fetchone()[0], 'paused')
        self.assertEqual(self.db.execute('SELECT state FROM import_files').fetchone()[0], 'copying')
        self.assertEqual(self.db.execute('SELECT count(*) FROM managed_archive').fetchone()[0], 0)

    def test_verification_requires_matching_hash_and_size(self):
        self.journal.transition_file(self.file, 'copying')
        for size, digest in [(2, 'a' * 64), (3, 'b' * 64)]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.journal.transition_file(self.file, 'verified', actual_size=size,
                    source_sha256='a' * 64, destination_sha256=digest)
        with self.assertRaises(ValueError):
            self.journal.transition_job('job', 'completed')
        self.journal.transition_file(self.file, 'verified', actual_size=3,
            source_sha256='a' * 64, destination_sha256='a' * 64)
        self.journal.transition_job('job', 'completed')
        with self.assertRaises(ValueError):
            self.journal.add_file('job', 'extra', 'extra', 0, 0)

    def test_pause_resume_resets_incomplete_copy(self):
        self.journal.transition_file(self.file, 'copying')
        self.journal.transition_job('job', 'paused')
        self.journal.transition_job('job', 'running')
        self.assertEqual(self.db.execute('SELECT state FROM import_files').fetchone()[0], 'pending')

    def test_invalid_transitions_and_duplicate_paths_rejected(self):
        with self.assertRaises(ValueError):
            self.journal.transition_file(self.file, 'verified')
        with self.assertRaises(sqlite3.IntegrityError):
            self.journal.add_file('job', 'photo.jpg', 'other', 3, 42)

    def test_checkpoints_survive_reopening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.sqlite'
            connection = sqlite3.connect(path)
            connection.executescript(SCHEMA_SQL)
            journal = ImportJournal(connection)
            journal.create_job('persisted', '/source', '/destination')
            file_id = journal.add_file('persisted', 'file', 'originals/file', 3, 42)
            journal.transition_file(file_id, 'copying')
            journal.transition_job('persisted', 'failed', error='disk disconnected')
            connection.close()
            connection = sqlite3.connect(path)
            try:
                journal = ImportJournal(connection)
                self.assertEqual(connection.execute(
                    'SELECT state,error FROM import_jobs'
                ).fetchone(), ('failed', 'disk disconnected'))
                journal.transition_job('persisted', 'running')
                self.assertEqual(connection.execute(
                    'SELECT state,source_mtime_ns FROM import_files'
                ).fetchone(), ('pending', 42))
            finally:
                connection.close()

    def test_failed_migration_rolls_back_schema_changes(self):
        connection = sqlite3.connect(':memory:')
        try:
            connection.executescript(SCHEMA_SQL)
            connection.execute('CREATE TABLE import_files (id INTEGER)')
            with self.assertRaises(sqlite3.OperationalError):
                migrate(connection)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name='import_jobs'"
            ).fetchone())
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)
        finally:
            connection.close()
