from models import ParseResult


def validate_messages(messages: list[dict], unknown_format: bool = False) -> tuple[str, list[str]]:
    """Assign parse status without hiding incomplete or unsupported records."""

    if unknown_format:
        # Unknown formats are expected while KakaoTalk export conventions are still
        # being discovered, so report them clearly instead of crashing.
        return "unknown_format", ["Unknown KakaoTalk export format."]

    warnings = []
    if not messages:
        return "failed", ["No messages were parsed."]

    partial = False
    for message in messages:
        missing = message.get("missing_fields") or []
        if missing:
            # A parsed message with missing fields is still useful, but the result
            # should be marked partial so future code knows to handle it carefully.
            partial = True
            warnings.append(
                f"{message.get('localId')} missing fields: {', '.join(missing)}"
            )

    return ("partial" if partial else "success"), warnings


def validate_result(result: ParseResult) -> ParseResult:
    """Attach validation status and warnings to a full parse result."""

    status, warnings = validate_messages(
        result.messages,
        unknown_format=result.detected_format == "kakaotalk_unknown",
    )
    result.parse_status = status
    result.warnings.extend(warnings)
    return result
