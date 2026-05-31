import re

from base_parser import BaseParser
from detector import KO_MOBILE_FORMAT
from message_classifier import classify_message_content
from models import PreprocessedInput, RawMessage


# Korean mobile txt message assumption:
# "2026-05-25 오전 9:05, Alice : 안녕"
# Groups are date, Korean AM/PM token, hour, minute, sender, and message body.
MSG_KO = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(오전|오후)\s+(\d{1,2}):(\d{2}),\s+([^:]+?)\s+:\s+(.*)$"
)
# Korean date divider assumption: "2026년 5월 25일 월요일".
# These lines organize the transcript and should not be appended to messages.
DATE_DIVIDER_KO = re.compile(r"^\d{4}년\s+\d{1,2}월\s+\d{1,2}일")


class KakaoMobileTxtKoParser(BaseParser):
    """Parser for the observed Korean mobile KakaoTalk `.txt` transcript format."""

    format_id = KO_MOBILE_FORMAT

    def can_parse(self, preprocessed: PreprocessedInput) -> bool:
        # AM/PM tokens are a strong clue for Korean-setting mobile exports.
        return "오전" in preprocessed.text or "오후" in preprocessed.text

    def parse(self, preprocessed: PreprocessedInput) -> list[RawMessage]:
        """Extract message fields while preserving source line numbers."""

        messages: list[RawMessage] = []
        lines = preprocessed.text.split("\n")
        start_idx = _skip_header(lines)

        last_message: RawMessage | None = None

        for line_number, raw in enumerate(lines[start_idx:], start=start_idx + 1):
            if not raw.strip():
                continue

            match = MSG_KO.match(raw)
            if not match:
                if DATE_DIVIDER_KO.match(raw):
                    continue
                if last_message is not None:
                    _append_continuation(last_message, raw, line_number)
                continue

            date, ampm, hour, minute, sender, content = match.groups()
            hour_int = int(hour)
            # Normalize Korean 12-hour time into 24-hour time for one stable schema.
            if ampm == "오후" and hour_int != 12:
                hour_int += 12
            if ampm == "오전" and hour_int == 12:
                hour_int = 0

            timestamp = f"{date} {hour_int:02d}:{minute}"
            message_type, metadata = classify_message_content(content)
            last_message = RawMessage(
                raw=raw,
                timestamp=timestamp,
                sender=sender,
                content=content,
                type=message_type,
                metadata=metadata,
                source_line_start=line_number,
                source_line_end=line_number,
            )
            messages.append(last_message)

        return messages


def _skip_header(lines: list[str]) -> int:
    """Skip the filename/saved-date header used by mobile txt exports."""

    non_blank = 0
    for idx, line in enumerate(lines):
        if line.strip():
            non_blank += 1
            if non_blank == 2:
                return idx + 1
    return len(lines)


def _message_type(content: str) -> str:
    """Map exact KakaoTalk placeholders to internal message types."""

    message_type, _ = classify_message_content(content)
    return message_type


def _append_continuation(message: RawMessage, raw: str, line_number: int) -> None:
    """Attach a physical line that belongs to the previous KakaoTalk message."""

    # KakaoTalk mobile txt exports repeat timestamp/sender only on the first line
    # of a multiline message. Continuation lines should stay in the message body.
    message.raw = f"{message.raw}\n{raw}"
    message.content = f"{message.content}\n{raw}"
    message.source_line_end = line_number
    # A media placeholder followed by extra text is no longer just a placeholder.
    message.type, message.metadata = classify_message_content(message.content)
