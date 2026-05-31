import re
from datetime import datetime
from bisect import bisect

from models import RawMessage


URL_RE = re.compile(r"https?://\S+")


def normalize_messages(raw_messages: list[RawMessage]) -> list[dict]:
    """Convert parser-specific RawMessage objects into one stable JSON schema."""

    normalized = []
    for index, message in enumerate(raw_messages, start=1):
        # Preserve parser metadata, then add shared metadata used by search/analysis.
        metadata = dict(message.metadata)
        urls = URL_RE.findall(message.content)
        metadata["hasUrl"] = bool(urls)
        metadata["urls"] = urls

        # Every parser can use its own timestamp shape; normalize here so downstream
        # code does not need to know which export format produced the message.
        timestamp = _normalize_timestamp(message.timestamp)
        time_of_day, season = _time_context(timestamp)
        normalized.append(
            {
                "localId": f"msg_{index:06d}",
                "timestamp": timestamp,
                "sender": message.sender.strip() if message.sender else None,
                "content": message.content.strip(),
                "type": message.type,
                "time_of_day": time_of_day,
                "season": season,
                "source_line_start": message.source_line_start,
                "source_line_end": message.source_line_end,
                "source_record_number": message.source_record_number,
                "raw": message.raw,
                # Missing fields are kept in the JSON so imperfect parses are visible.
                "missing_fields": message.missing_fields,
                "metadata": metadata,
            }
        )
    return normalized


def to_legacy_messages(messages: list[dict]) -> list[dict]:
    """Return the older renderer-compatible list shape used by Electron today."""

    legacy_messages = []
    for message in messages:
        if message.get("timestamp") is None or message.get("sender") is None:
            # The old renderer shape cannot represent senderless system rows yet.
            # Rich metadata export keeps them; default export skips them for compatibility.
            continue
        legacy_messages.append(
            {
                "timestamp": _legacy_timestamp(message["timestamp"]),
                "sender": message["sender"],
                "content": message["content"],
                "time_of_day": message["time_of_day"],
                "season": message["season"],
            }
        )
    return legacy_messages


def _normalize_timestamp(timestamp: str | None) -> str | None:
    """Normalize known KakaoTalk timestamp strings into ISO-like seconds."""

    if timestamp is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(timestamp, fmt)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            continue
    return timestamp


def _legacy_timestamp(timestamp: str) -> str:
    """Convert normalized timestamps back to the old minute-level format."""

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(timestamp, fmt)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return timestamp


def _time_context(timestamp: str | None) -> tuple[str | None, str | None]:
    """Derive existing time-of-day and season fields from normalized timestamps."""

    if timestamp is None:
        return None, None
    try:
        dt = datetime.fromisoformat(timestamp)
    except ValueError:
        return None, None

    hour_edges = [4, 11, 16, 21]
    tod_labels = ("night", "morning", "afternoon", "evening", "night")
    tod = tod_labels[bisect(hour_edges, dt.hour)]

    month_edges = [2, 5, 8, 11]
    season_labels = ("winter", "spring", "summer", "fall", "winter")
    season = season_labels[bisect(month_edges, dt.month)]
    return tod, season
