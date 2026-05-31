import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from chatParse import ChatParser  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "parser"


class PcCsvParserTests(unittest.TestCase):
    def test_parses_csv_multiline_and_deleted_system_row(self):
        parser = ChatParser()
        parser.parse_chat_file(FIXTURES / "kakao_pc_csv_ko_sample.csv")

        result = parser.results[0]
        self.assertEqual(len(result.messages), 5)
        self.assertEqual(result.messages[1]["content"], "multi\nline message")
        self.assertEqual(result.messages[2]["type"], "system")
        self.assertEqual(result.messages[2]["timestamp"], "2026-05-27T22:53:02")
        self.assertTrue(result.messages[2]["metadata"]["timestampInheritedFromPreviousMessage"])
        self.assertEqual(result.messages[4]["type"], "photo")
        self.assertEqual(result.messages[4]["metadata"]["attachmentCount"], 5)
        self.assertEqual(result.parse_status, "partial")


if __name__ == "__main__":
    unittest.main()
