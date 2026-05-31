import re
from typing import Any


# KakaoTalk can represent multiple photos as one placeholder message.
# Korean assumption: "사진 5장"; English assumption: "5 photos" or "1 photo".
KO_MULTI_PHOTO_RE = re.compile(r"^사진\s+(\d+)장$")
EN_MULTI_PHOTO_RE = re.compile(r"^(\d+)\s+photos?$", re.IGNORECASE)


def classify_message_content(content: str) -> tuple[str, dict[str, Any]]:
    """Classify KakaoTalk placeholder text without matching actual media files."""

    stripped = content.strip()

    if match := KO_MULTI_PHOTO_RE.match(stripped):
        return _media("photo", stripped, int(match.group(1)))
    if match := EN_MULTI_PHOTO_RE.match(stripped):
        return _media("photo", stripped, int(match.group(1)))

    if stripped == "The message has been deleted.":
        return "system", {"systemSubtype": "deleted_message"}

    exact_types = {
        "사진": ("photo", 1),
        "Photo": ("photo", 1),
        "동영상": ("video", 1),
        "Video": ("video", 1),
        "음성메시지": ("voice", 1),
        "이모티콘": ("emoticon", 1),
    }
    if stripped in exact_types:
        message_type, count = exact_types[stripped]
        return _media(message_type, stripped, count)

    return "text", {}


def _media(message_type: str, placeholder: str, count: int) -> tuple[str, dict[str, Any]]:
    return (
        message_type,
        {
            "placeholderText": placeholder,
            "attachmentCount": count,
        },
    )
