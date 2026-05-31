from pathlib import Path
from typing import Any
import json

from detector import UNKNOWN_FORMAT, detect_format
from exporter import export_json
from models import ParseResult
from normalizer import normalize_messages, to_legacy_messages
from preprocessor import preprocess_file
from registry import ParserRegistry
from validator import validate_result


class ChatParser:
    """
    Compatibility facade for the Electron app.

    Internally this now runs:
    preprocessor -> detector -> registry -> format parser -> normalizer -> validator.
    The public `messages` list remains in the older JSON shape so existing renderer
    code can keep reading `timestamp`, `sender`, `content`, `time_of_day`, and `season`.
    """

    def __init__(self):
        self.messages: list[dict[str, Any]] = []
        self.results: list[ParseResult] = []
        self._registry = ParserRegistry()

    def parse_chat_file(self, file_path: str | Path) -> None:
        """Parse one transcript file and accumulate legacy-compatible messages."""

        # Step 1: read safely, normalize line endings, and keep a detection sample.
        preprocessed = preprocess_file(file_path)
        # Step 2: detect the export structure before choosing parser logic.
        detection = detect_format(preprocessed)
        # Step 3: use a registry so each format parser stays separate.
        parser = self._registry.get(detection.detected_format)

        if detection.detected_format == UNKNOWN_FORMAT:
            # Unknown formats should preserve evidence for future parser work.
            # We keep sample lines and raw_text_preserved instead of failing silently.
            result = ParseResult(
                parse_status="unknown_format",
                source=preprocessed.source,
                file_type=preprocessed.file_type,
                detected_format=detection.detected_format,
                locale_guess=detection.locale_guess,
                confidence=detection.confidence,
                parser_used=parser.__class__.__name__,
                messages=[],
                warnings=[],
                sample_lines=preprocessed.sample_lines,
                raw_text_preserved=bool(preprocessed.raw_text),
                reason=detection.reason,
            )
            result = validate_result(result)
        else:
            # Step 4: format parser extracts raw fields; normalizer makes the schema stable.
            raw_messages = parser.parse(preprocessed)
            normalized_messages = normalize_messages(raw_messages)
            result = ParseResult(
                parse_status="success",
                source=preprocessed.source,
                file_type=preprocessed.file_type,
                detected_format=detection.detected_format,
                locale_guess=detection.locale_guess,
                confidence=detection.confidence,
                parser_used=parser.__class__.__name__,
                messages=normalized_messages,
                warnings=[],
                sample_lines=preprocessed.sample_lines,
                raw_text_preserved=bool(preprocessed.raw_text),
                reason=detection.reason,
            )
            result = validate_result(result)

        # Step 5: keep both rich results and the old message list during migration.
        self.results.append(result)
        self.messages.extend(to_legacy_messages(result.messages))

    def exportJson(self, out_path: str | Path) -> None:
        """Export the old message-list schema expected by the current renderer."""

        self.messages.sort(key=lambda m: m["timestamp"])
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=2)

    def exportResultJson(self, out_path: str | Path) -> None:
        """Export the richer parser report for debugging and future importer work."""

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        export_json(self._combined_result(), out, include_metadata=True)

    def _combined_result(self) -> ParseResult:
        """Merge results from multiple parsed files into one report."""

        combined = ParseResult(
            parse_status=_combined_status(self.results),
            source=", ".join(result.source for result in self.results),
            file_type="mixed" if len({r.file_type for r in self.results}) > 1 else (self.results[0].file_type if self.results else "unknown"),
            detected_format="mixed" if len({r.detected_format for r in self.results}) > 1 else (self.results[0].detected_format if self.results else UNKNOWN_FORMAT),
            locale_guess="mixed" if len({r.locale_guess for r in self.results}) > 1 else (self.results[0].locale_guess if self.results else "unknown"),
            confidence=min((r.confidence for r in self.results), default=0.0),
            parser_used=", ".join(sorted({result.parser_used for result in self.results})),
            messages=[],
            warnings=[warning for result in self.results for warning in result.warnings],
            sample_lines=[
                line
                for result in self.results
                for line in result.sample_lines
            ],
            raw_text_preserved=any(result.raw_text_preserved for result in self.results),
            reason=self.results[0].reason
            if len(self.results) == 1
            else "Combined parse result from ChatParser compatibility facade.",
        )
        combined.messages = _renumber_messages(
            message
            for result in self.results
            for message in result.messages
        )
        return combined


def _combined_status(results: list[ParseResult]) -> str:
    if not results:
        return "failed"
    statuses = {result.parse_status for result in results}
    if statuses == {"success"}:
        return "success"
    if "success" in statuses or "partial" in statuses:
        return "partial"
    if "unknown_format" in statuses:
        return "unknown_format"
    return "failed"


def _renumber_messages(messages) -> list[dict[str, Any]]:
    renumbered = []
    for index, message in enumerate(messages, start=1):
        renumbered.append({**message, "localId": f"msg_{index:06d}"})
    return renumbered
