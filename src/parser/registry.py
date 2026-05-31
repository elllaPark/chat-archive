from base_parser import BaseParser
from detector import (
    EN_MOBILE_FORMAT,
    KO_MOBILE_FORMAT,
    PC_CSV_EN_FORMAT,
    PC_CSV_KO_FORMAT,
    UNKNOWN_FORMAT,
)
from parsers.kakao_mobile_txt_en import KakaoMobileTxtEnParser
from parsers.kakao_mobile_txt_ko import KakaoMobileTxtKoParser
from parsers.kakao_pc_csv import KakaoPcCsvParser
from parsers.unknown_parser import UnknownKakaoParser


class ParserRegistry:
    """Map detector format IDs to parser classes.

    This avoids one large parser full of if/else branches. Adding a new format
    should mean adding one parser class and one registry entry.
    """

    def __init__(self):
        # Unknown parser is reused as the safe fallback for any unsupported format.
        unknown = UnknownKakaoParser()
        self._parsers: dict[str, BaseParser] = {
            KO_MOBILE_FORMAT: KakaoMobileTxtKoParser(),
            EN_MOBILE_FORMAT: KakaoMobileTxtEnParser(),
            PC_CSV_KO_FORMAT: KakaoPcCsvParser(PC_CSV_KO_FORMAT),
            PC_CSV_EN_FORMAT: KakaoPcCsvParser(PC_CSV_EN_FORMAT),
            UNKNOWN_FORMAT: unknown,
        }
        self._unknown = unknown

    def get(self, format_id: str) -> BaseParser:
        """Return the best parser for a detected format, or the unknown parser."""
        return self._parsers.get(format_id, self._unknown)
