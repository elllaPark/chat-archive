import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "parser"))

from detector import (  # noqa: E402
    EN_MOBILE_FORMAT,
    KO_MOBILE_FORMAT,
    PC_CSV_KO_FORMAT,
    UNKNOWN_FORMAT,
    detect_format,
)
from preprocessor import preprocess_file  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "parser"


class DetectorTests(unittest.TestCase):
    def test_detects_korean_mobile_txt(self):
        result = detect_format(preprocess_file(FIXTURES / "kakao_mobile_txt_ko_sample.txt"))

        self.assertEqual(result.detected_format, KO_MOBILE_FORMAT)
        self.assertEqual(result.locale_guess, "ko")
        self.assertGreater(result.confidence, 0.8)

    def test_detects_english_mobile_txt(self):
        result = detect_format(preprocess_file(FIXTURES / "kakao_mobile_txt_en_sample.txt"))

        self.assertEqual(result.detected_format, EN_MOBILE_FORMAT)
        self.assertEqual(result.locale_guess, "en")
        self.assertGreater(result.confidence, 0.6)

    def test_detects_pc_csv(self):
        result = detect_format(preprocess_file(FIXTURES / "kakao_pc_csv_ko_sample.csv"))

        self.assertEqual(result.detected_format, PC_CSV_KO_FORMAT)
        self.assertEqual(result.file_type, "csv")
        self.assertIn("csv_header_date_user_message", result.matched_signatures)

    def test_unknown_format_is_expected(self):
        result = detect_format(preprocess_file(FIXTURES / "unknown_sample.txt"))

        self.assertEqual(result.detected_format, UNKNOWN_FORMAT)
        self.assertEqual(result.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
