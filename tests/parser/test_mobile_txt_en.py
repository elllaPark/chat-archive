import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from chatParse import ChatParser  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "parser"


class MobileTxtEnParserTests(unittest.TestCase):
    def test_preserves_multiline_message_content(self):
        parser = ChatParser()
        parser.parse_chat_file(FIXTURES / "kakao_mobile_txt_en_multiline_sample.txt")

        self.assertEqual(len(parser.messages), 3)
        self.assertEqual(
            parser.messages[0]["content"],
            "first line\nsecond line\nhttps://example.com/english",
        )
        self.assertEqual(parser.results[0].detected_format, "kakaotalk_mobile_txt_en_v1")
        self.assertEqual(parser.results[0].messages[0]["source_line_start"], 5)
        self.assertEqual(parser.results[0].messages[0]["source_line_end"], 7)
        self.assertTrue(parser.results[0].messages[0]["metadata"]["hasUrl"])
        self.assertEqual(parser.results[0].messages[2]["type"], "photo")
        self.assertEqual(parser.results[0].messages[2]["metadata"]["attachmentCount"], 5)


if __name__ == "__main__":
    unittest.main()
