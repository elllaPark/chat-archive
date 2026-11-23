import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from detector import KO_MOBILE_FORMAT, UNKNOWN_FORMAT  # noqa: E402
from registry import ParserRegistry  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_returns_format_parser(self):
        parser = ParserRegistry().get(KO_MOBILE_FORMAT)

        self.assertEqual(parser.format_id, KO_MOBILE_FORMAT)

    def test_unknown_falls_back_to_unknown_parser(self):
        parser = ParserRegistry().get("not_real")

        self.assertEqual(parser.format_id, UNKNOWN_FORMAT)


if __name__ == "__main__":
    unittest.main()
