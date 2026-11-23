import json
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archive import import_archive  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "archive"


class ArchiveImporterTests(unittest.TestCase):
    def test_imports_normalized_messages_and_deterministic_asset_metadata(self):
        messages = json.loads(
            (FIXTURES / "normalized_messages.json").read_text(encoding="utf-8")
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            assets_root = temp / "media"
            (assets_root / "nested").mkdir(parents=True)
            (assets_root / "20260525_221503_123.JPG").write_bytes(b"fake-jpeg")
            (assets_root / "nested" / "notes.txt").write_text("hello", encoding="utf-8")
            (assets_root / "unknown.bin").write_bytes(b"abc")

            database = import_archive(
                messages,
                assets_root,
                temp / "archive.sqlite",
                source_metadata={"formatIds": ["kakaotalk_mobile_txt_ko_v1"]},
            )

            with closing(sqlite3.connect(database)) as connection:
                connection.row_factory = sqlite3.Row
                imported = connection.execute("SELECT * FROM imports").fetchone()
                stored_messages = connection.execute(
                    "SELECT * FROM messages ORDER BY id"
                ).fetchall()
                assets = connection.execute(
                    "SELECT * FROM assets ORDER BY local_id"
                ).fetchall()

            self.assertEqual(
                (
                    imported["schema_version"],
                    imported["message_count"],
                    imported["asset_count"],
                ),
                (1, 2, 3),
            )
            self.assertEqual(stored_messages[1]["type"], "photo")
            self.assertEqual(
                json.loads(stored_messages[1]["metadata_json"])["attachmentCount"],
                1,
            )
            self.assertEqual(
                [
                    (
                        row["local_id"],
                        row["original_relative_path"],
                        row["asset_type"],
                    )
                    for row in assets
                ],
                [
                    ("asset_000001", "20260525_221503_123.JPG", "photo"),
                    ("asset_000002", "nested/notes.txt", "text_file"),
                    ("asset_000003", "unknown.bin", "unknown"),
                ],
            )
            self.assertEqual(assets[0]["mime_type"], "image/jpeg")
            self.assertEqual(assets[0]["size_bytes"], 9)
            self.assertEqual(assets[0]["filename_timestamp"], "2026-05-25T22:15:03")
            self.assertEqual(assets[0]["filename_sequence"], 123)


if __name__ == "__main__":
    unittest.main()
