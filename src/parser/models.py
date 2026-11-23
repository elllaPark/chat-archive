from dataclasses import dataclass, field
from typing import Any


@dataclass
class PreprocessedInput:
    """File text after safe reading, before any format-specific parsing."""

    source: str
    file_type: str
    encoding: str
    # Keep the exact decoded file text so unknown formats can be inspected later.
    raw_text: str
    # Normalized text is easier for parsers because all line endings are "\n".
    text: str
    # Detector only needs a small beginning sample, not the whole private chat.
    sample_lines: list[str]


@dataclass
class DetectionResult:
    """Detector output used by the registry to choose a parser."""

    source: str
    file_type: str
    detected_format: str
    locale_guess: str
    confidence: float
    matched_signatures: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class RawMessage:
    """Message fields as extracted by one format-specific parser."""

    raw: str
    timestamp: str | None = None
    sender: str | None = None
    content: str = ""
    type: str = "text"
    source_line_start: int | None = None
    source_line_end: int | None = None
    source_record_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    """Final parser report used for metadata export and debugging."""

    parse_status: str
    source: str
    file_type: str
    detected_format: str
    locale_guess: str
    confidence: float
    parser_used: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sample_lines: list[str] = field(default_factory=list)
    raw_text_preserved: bool = False
    reason: str = ""
