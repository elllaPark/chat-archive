from base_parser import BaseParser
from detector import UNKNOWN_FORMAT
from models import PreprocessedInput, RawMessage


class UnknownKakaoParser(BaseParser):
    """Safe parser for unsupported formats.

    It intentionally extracts no messages. The caller still keeps sample lines
    and the raw-text-preserved flag so a future parser can be built from the
    unknown-format report instead of silently losing data.
    """

    format_id = UNKNOWN_FORMAT

    def can_parse(self, preprocessed: PreprocessedInput) -> bool:
        return True

    def parse(self, preprocessed: PreprocessedInput) -> list[RawMessage]:
        return []
