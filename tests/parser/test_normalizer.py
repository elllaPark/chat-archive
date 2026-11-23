import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from models import RawMessage  # noqa: E402
from normalizer import normalize_messages, to_legacy_messages  # noqa: E402


class NormalizerTests(unittest.TestCase):
    def test_normalizes_timestamp_and_preserves_legacy_shape(self):
        messages = normalize_messages(
            [
                RawMessage(
                    raw="2026-05-27 22:52:01,Alice,https://example.com",
                    timestamp="2026-05-27 22:52:01",
                    sender="Alice",
                    content="https://example.com",
                )
            ]
        )

        self.assertEqual(messages[0]["timestamp"], "2026-05-27T22:52:01")
        self.assertTrue(messages[0]["metadata"]["hasUrl"])
        self.assertEqual(
            to_legacy_messages(messages)[0],
            {
                "timestamp": "2026-05-27 22:52",
                "sender": "Alice",
                "content": "https://example.com",
                "time_of_day": "night",
                "season": "summer",
            },
        )


if __name__ == "__main__":
    unittest.main()
