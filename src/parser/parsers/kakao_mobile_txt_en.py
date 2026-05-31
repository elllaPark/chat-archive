import re

from base_parser import BaseParser
from detector import EN_MOBILE_FORMAT
from message_classifier import classify_message_content
from models import PreprocessedInput, RawMessage


MONTH_LOOKUP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# English mobile txt message assumption:
# "Jul 16, 2020 22:23, Alice : hello"
# Groups are month, day, year, hour, minute, sender, and message body.
MSG_EN_MONTH = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})\s+(\d{2}):(\d{2}),\s+([^:]+?)\s+:\s+(.*)$",
    re.IGNORECASE,
)
# English date divider assumption: "Thursday, July 16, 2020".
# It separates days in the transcript and is not message content.
DATE_DIVIDER_EN = re.compile(
    r"^[A-Za-z]+,\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
    re.IGNORECASE,
)


class KakaoMobileTxtEnParser(BaseParser):
    """Parser for the observed English mobile KakaoTalk `.txt` transcript format."""

    format_id = EN_MOBILE_FORMAT

    def can_parse(self, preprocessed: PreprocessedInput) -> bool:
        # This header appeared in the inspected English-setting mobile export.
        return "Date Saved" in preprocessed.text

    def parse(self, preprocessed: PreprocessedInput) -> list[RawMessage]:
        """Extract English month-name timestamps into raw message records."""

        messages: list[RawMessage] = []
        lines = preprocessed.text.split("\n")
        start_idx = _skip_header(lines)

        last_message: RawMessage | None = None

        for line_number, raw in enumerate(lines[start_idx:], start=start_idx + 1):
            if not raw.strip():
                continue

            match = MSG_EN_MONTH.match(raw)
            if not match:
                if DATE_DIVIDER_EN.match(raw):
                    continue
                if last_message is not None:
                    _append_continuation(last_message, raw, line_number)
                continue

            month_name, day, year, hour, minute, sender, content = match.groups()
            # Convert month names to numbers before normalization creates ISO strings.
            month = MONTH_LOOKUP[month_name.lower()]
            timestamp = f"{year}-{month:02d}-{int(day):02d} {hour}:{minute}"
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
    """Map exact English KakaoTalk media placeholders to internal types."""

    message_type, _ = classify_message_content(content)
    return message_type


def _append_continuation(message: RawMessage, raw: str, line_number: int) -> None:
    """Attach a physical line that belongs to the previous KakaoTalk message."""

    # English mobile txt exports can also keep line breaks by omitting repeated
    # timestamp/sender fields on continuation lines.
    message.raw = f"{message.raw}\n{raw}"
    message.content = f"{message.content}\n{raw}"
    message.source_line_end = line_number
    message.type, message.metadata = classify_message_content(message.content)
