import json
from pathlib import Path

from models import ParseResult
from normalizer import to_legacy_messages


def export_json(result: ParseResult, out_path: str | Path, include_metadata: bool = False) -> None:
    """Write either legacy message JSON or the richer parser report."""

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Default output stays compatible with the current renderer. Metadata export is
    # opt-in because the UI does not consume the richer schema yet.
    payload = _to_dict(result) if include_metadata else to_legacy_messages(result.messages)
    with out.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _to_dict(result: ParseResult) -> dict:
    """Convert the dataclass result into plain JSON-serializable data."""

    return {
        "parse_status": result.parse_status,
        "source": result.source,
        "file_type": result.file_type,
        "detected_format": result.detected_format,
        "locale_guess": result.locale_guess,
        "confidence": result.confidence,
        "parser_used": result.parser_used,
        "messages": result.messages,
        "warnings": result.warnings,
        "sample_lines": result.sample_lines,
        "raw_text_preserved": result.raw_text_preserved,
        "reason": result.reason,
    }
