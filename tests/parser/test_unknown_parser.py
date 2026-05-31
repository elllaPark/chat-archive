import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from chatParse import ChatParser  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "parser"


class UnknownParserTests(unittest.TestCase):
    def test_unknown_format_returns_structured_result(self):
        parser = ChatParser()
        parser.parse_chat_file(FIXTURES / "unknown_sample.txt")

        result = parser.results[0]
        self.assertEqual(result.parse_status, "unknown_format")
        self.assertEqual(result.detected_format, "kakaotalk_unknown")
        self.assertEqual(parser.messages, [])
        self.assertTrue(result.raw_text_preserved)
        self.assertGreater(len(result.sample_lines), 0)


if __name__ == "__main__":
    unittest.main()
