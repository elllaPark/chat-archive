import errno
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from archive import filesystem as fs


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_native_publication_refuses_existing_unicode_target(self):
        source, target = self.root / 'source', self.root / '사진.bin'
        source.write_bytes(b'first')
        fs.publish_no_replace(source, target)
        self.assertFalse(source.exists())
        source.write_bytes(b'second')
        with self.assertRaises(FileExistsError):
            fs.publish_no_replace(source, target)
        self.assertEqual(target.read_bytes(), b'first')
        self.assertEqual(source.read_bytes(), b'second')

    def test_windows_dispatch_uses_nonreplacing_rename(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_bytes(b'source')
        with patch.object(fs, '_PLATFORM', 'win32'), patch.object(fs.os, 'rename') as rename:
            fs.publish_no_replace(source, target)
            rename.assert_called_once_with(source, target)

    def test_windows_lock_dispatch_acquires_and_releases_one_byte(self):
        fake = SimpleNamespace(locking=Mock(), LK_NBLCK=2, LK_UNLCK=0)
        with patch.object(fs, '_PLATFORM', 'win32'), patch.dict(sys.modules, {'msvcrt': fake}):
            with fs.writer_lock(self.root):
                self.assertEqual(fake.locking.call_args.args[1:], (2, 1))
            self.assertEqual(fake.locking.call_args.args[1:], (0, 1))
            self.assertEqual(fake.locking.call_count, 2)

    def test_windows_busy_lock_does_not_unlock_unacquired_lock(self):
        fake = SimpleNamespace(locking=Mock(side_effect=OSError(errno.EACCES, 'busy')),
                               LK_NBLCK=2, LK_UNLCK=0)
        with patch.object(fs, '_PLATFORM', 'win32'), patch.dict(sys.modules, {'msvcrt': fake}):
            with self.assertRaises(BlockingIOError):
                with fs.writer_lock(self.root):
                    self.fail('Should not acquire busy lock')
        self.assertEqual(fake.locking.call_count, 1)

    def test_lock_blocks_another_process(self):
        code = '''
import sys
from pathlib import Path
from archive.filesystem import writer_lock
try:
    with writer_lock(Path(sys.argv[1])):
        sys.exit(2)
except BlockingIOError:
    sys.exit(0)
'''
        with fs.writer_lock(self.root):
            result = subprocess.run([sys.executable, '-c', code, str(self.root)],
                                    env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with fs.writer_lock(self.root):
            pass

    def test_directory_sync_unsupported_is_distinct_from_disk_error(self):
        with patch.object(fs, '_PLATFORM', 'linux'), patch.object(fs.os, 'open', return_value=123), \
                patch.object(fs.os, 'close'), patch.object(fs.os, 'fsync', side_effect=OSError(errno.EINVAL, 'unsupported')):
            self.assertFalse(fs.sync_directory(self.root))
        with patch.object(fs, '_PLATFORM', 'linux'), patch.object(fs.os, 'open', return_value=123), \
                patch.object(fs.os, 'close'), patch.object(fs.os, 'fsync', side_effect=OSError(errno.EIO, 'disconnected')):
            with self.assertRaises(OSError):
                fs.sync_directory(self.root)
        with patch.object(fs, '_PLATFORM', 'win32'):
            self.assertFalse(fs.sync_directory(self.root))

    def test_probe_does_not_require_hardlinks(self):
        with patch.object(fs.os, 'link', side_effect=AssertionError('hard links forbidden')):
            capabilities = fs.probe_storage(self.root)
        self.assertTrue(capabilities['exclusivePublication'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unsupported_publication_is_reported(self):
        with patch.object(fs, 'publish_no_replace', side_effect=fs.UnsupportedStorageError(errno.ENOTSUP, 'unsupported')):
            with self.assertRaises(fs.UnsupportedStorageError):
                fs.probe_storage(self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reparse_point_is_identified(self):
        self.assertTrue(fs.is_link(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)))

    def test_owned_staging_recovery_preserves_unrecognized_files(self):
        record = {'id': 3, 'stored_relative_path': 'originals/photo.jpg'}
        owned = self.root / '.chat-stage-owned'
        owned.mkdir()
        (owned / 'owner.json').write_text(json.dumps(fs._owner('job', record)))
        (owned / 'payload.part').write_bytes(b'partial')
        unknown = self.root / '.chat-stage-user'
        unknown.mkdir()
        (unknown / 'keep.txt').write_bytes(b'keep')
        self.assertEqual(fs.recover_staging(self.root, 'job', [record]), ['unrecognized_staging_retained'])
        self.assertFalse(owned.exists())
        self.assertEqual((unknown / 'keep.txt').read_bytes(), b'keep')

    def test_owned_stage_cleans_up_after_normal_failure(self):
        record = {'id': 3, 'stored_relative_path': 'originals/photo.jpg'}
        with self.assertRaises(RuntimeError):
            with fs.owned_stage(self.root, 'job', record) as temporary:
                temporary.write_bytes(b'partial')
                raise RuntimeError('interrupted')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_files_inside_marked_stage_are_not_deleted(self):
        record = {'id': 3, 'stored_relative_path': 'originals/photo.jpg'}
        stage = self.root / '.chat-stage-owned'
        stage.mkdir()
        (stage / 'owner.json').write_text(json.dumps(fs._owner('job', record)))
        (stage / 'unrelated.bin').write_bytes(b'keep')
        fs.recover_staging(self.root, 'job', [record])
        self.assertTrue((stage / 'unrelated.bin').exists())

    def test_fallback_publication_without_exclusive_rename(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_bytes(b'payload' * 1000)
        unsupported = fs.UnsupportedStorageError(errno.ENOTSUP, 'no exclusive rename')
        with patch.object(fs, '_native_publish', side_effect=unsupported):
            self.assertEqual(fs.publish_no_replace(source, target), 'exclusive_create_verified')
            source.write_bytes(b'keep second')
            with self.assertRaises(FileExistsError):
                fs.publish_no_replace(source, target)
        self.assertEqual(target.read_bytes(), b'payload' * 1000)
        self.assertEqual(source.read_bytes(), b'keep second')
        self.assertFalse(list(self.root.glob('.chat-publish-*')))

    def _interrupt_publication(self):
        source, target = self.root / 'source', self.root / 'target'
        data = b'known payload bytes' * 1000
        source.write_bytes(data)
        def interrupt(folder, owner):
            with target.open('xb') as output:
                output.write(data[:100])
                output.flush()
                owner['identity'] = fs._identity(os.fstat(output.fileno()))
                fs._write_owner(folder, owner)
            raise OSError('simulated disconnect')
        with patch.object(fs, '_finish_publication', side_effect=interrupt):
            with self.assertRaises(OSError):
                fs._fallback_publish(source, target)
        return target, data

    def test_fallback_partial_copy_resumes_from_proven_prefix(self):
        target, data = self._interrupt_publication()
        self.assertEqual(target.stat().st_size, 100)
        fs.recover_publications(self.root)
        self.assertEqual(target.read_bytes(), data)
        self.assertFalse(list(self.root.glob('.chat-publish-*')))

    def test_changed_partial_file_is_preserved_as_conflict(self):
        target, _ = self._interrupt_publication()
        target.write_bytes(b'changed by someone else')
        with self.assertRaises(FileExistsError):
            fs.recover_publications(self.root)
        self.assertEqual(target.read_bytes(), b'changed by someone else')
        self.assertTrue(list(self.root.glob('.chat-publish-*')))

    def test_missing_creation_identity_is_not_guessed(self):
        target, _ = self._interrupt_publication()
        folder = next(self.root.glob('.chat-publish-*'))
        owner = json.loads((folder / 'owner.json').read_text())
        owner['identity'] = None
        fs._write_owner(folder, owner)
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            fs.recover_publications(self.root)
        self.assertEqual(target.read_bytes(), before)
