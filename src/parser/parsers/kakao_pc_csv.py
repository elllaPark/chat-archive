import csv
from io import StringIO

from base_parser import BaseParser
from message_classifier import classify_message_content
from models import PreprocessedInput, RawMessage


class KakaoPcCsvParser(BaseParser):
    """Parser for KakaoTalk PC CSV exports with Date/User/Message columns."""

    def __init__(self, format_id: str):
        self.format_id = format_id

    def can_parse(self, preprocessed: PreprocessedInput) -> bool:
        # CSV structure is confirmed by the detector; this is a simple sanity check.
        return preprocessed.file_type == "csv"

    def parse(self, preprocessed: PreprocessedInput) -> list[RawMessage]:
        """Parse CSV records, including quoted multiline messages."""

        messages: list[RawMessage] = []
        # Deleted/system CSV rows can be missing their own Date/User. The user
        # confirmed they should inherit the previous valid timestamp.
        previous_timestamp: str | None = None
        previous_local_index: int | None = None

        # csv.DictReader preserves quoted newlines inside Message cells, which is
        # why this parser should not split CSV files by physical lines manually.
        reader = csv.DictReader(StringIO(preprocessed.text))
        for record_number, row in enumerate(reader, start=1):
            date = (row.get("Date") or "").strip()
            sender = (row.get("User") or "").strip()
            content = row.get("Message") or ""
            message_type, metadata = classify_message_content(content)

            if not date and not sender and content.strip():
                # Assumption from inspected exports: an empty Date/User row with
                # text such as "The message has been deleted." is a system event.
                metadata.update(
                    {
                        "systemSubtype": _system_subtype(content),
                        "previousMessageLocalIndex": previous_local_index,
                        "timestampInheritedFromPreviousMessage": previous_timestamp is not None,
                    }
                )
                messages.append(
                    RawMessage(
                        raw=content,
                        timestamp=previous_timestamp,
                        sender=None,
                        content=content,
                        type="system",
                        source_record_number=record_number,
                        metadata=metadata,
                        missing_fields=["sender"] + ([] if previous_timestamp else ["timestamp"]),
                    )
                )
                continue

            message = RawMessage(
                raw=content,
                timestamp=date or None,
                sender=sender or None,
                content=content,
                type=message_type,
                metadata=metadata,
                source_record_number=record_number,
                missing_fields=_missing_fields(date, sender),
            )
            messages.append(message)

            if date:
                # Keep the previous valid timestamp available for later system rows.
                previous_timestamp = date
                previous_local_index = len(messages)

        return messages


def _message_type(content: str) -> str:
    """Classify exact placeholders without trying to match media files yet."""

    message_type, _ = classify_message_content(content)
    return message_type


def _system_subtype(content: str) -> str:
    """Describe known system-event text for future UI/analysis features."""

    if content.strip() == "The message has been deleted.":
        return "deleted_message"
    return "unknown"


def _missing_fields(date: str, sender: str) -> list[str]:
    """Record missing fields so validation can mark partial parses."""

    missing = []
    if not date:
        missing.append("timestamp")
    if not sender:
        missing.append("sender")
    return missing
