import csv
import re
from io import StringIO

from models import DetectionResult, PreprocessedInput


# Format IDs are intentionally about observed structure, not only language.
# KakaoTalk locale behavior may vary by app version, OS, or export method.
UNKNOWN_FORMAT = "kakaotalk_unknown"

KO_MOBILE_FORMAT = "kakaotalk_mobile_txt_ko_v1"
EN_MOBILE_FORMAT = "kakaotalk_mobile_txt_en_v1"
PC_CSV_KO_FORMAT = "kakaotalk_pc_csv_ko_v1"
PC_CSV_EN_FORMAT = "kakaotalk_pc_csv_en_v1"

# Any Hangul character is a weak locale clue, useful only as one signal.
HANGUL_RE = re.compile(r"[가-힣]")
# Korean mobile txt header assumption: "저장한 날짜 : 2026-05-27 오후 4:13".
KO_SAVED_RE = re.compile(r"저장한 날짜\s*:")
# Korean mobile txt date divider assumption: "2019년 3월 30일 토요일".
KO_DATE_DIVIDER_RE = re.compile(r"\d{4}년\s+\d{1,2}월\s+\d{1,2}일")
# Korean mobile txt message assumption: "2019-03-30 오전 4:39, sender : content".
KO_MESSAGE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\s+(오전|오후)\s+\d{1,2}:\d{2},\s+.+?\s+:\s+")

# English mobile txt header assumption: "Date Saved : Apr 23, 2021 14:55".
EN_SAVED_RE = re.compile(r"Date Saved\s*:", re.IGNORECASE)
# English mobile txt date divider assumption: "Thursday, July 16, 2020".
EN_DATE_DIVIDER_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"\d{1,2},\s+\d{4}",
    re.IGNORECASE,
)
# English mobile txt message assumption: "Jul 16, 2020 22:23, sender : content".
EN_MESSAGE_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2},\s+.+?\s+:\s+",
    re.IGNORECASE,
)


def detect_format(preprocessed: PreprocessedInput) -> DetectionResult:
    """Match lightweight signatures before choosing a parser."""

    text_sample = "\n".join(preprocessed.sample_lines)
    signatures: list[str] = []

    # CSV exports have a different record structure, so detect them separately.
    if preprocessed.file_type == "csv":
        return _detect_csv(preprocessed, text_sample)

    if preprocessed.file_type == "txt":
        # Korean mobile txt is identified by multiple independent signatures.
        # Requiring more than one reduces false positives from ordinary Korean text.
        if KO_SAVED_RE.search(text_sample):
            signatures.append("korean_saved_date_header")
        if KO_DATE_DIVIDER_RE.search(text_sample):
            signatures.append("korean_date_divider")
        if KO_MESSAGE_RE.search(text_sample):
            signatures.append("korean_message_line")

        if len(signatures) >= 2:
            return DetectionResult(
                source=preprocessed.source,
                file_type=preprocessed.file_type,
                detected_format=KO_MOBILE_FORMAT,
                locale_guess="ko",
                confidence=min(0.55 + (len(signatures) * 0.15), 0.98),
                matched_signatures=signatures,
                reason="Matched Korean mobile text export signatures.",
            )

        # English txt detection is based on the observed export sample. We avoid
        # calling it "legacy" because we have not checked a current English export.
        if EN_SAVED_RE.search(text_sample):
            signatures.append("english_saved_date_header")
        if EN_DATE_DIVIDER_RE.search(text_sample):
            signatures.append("english_date_divider")
        if EN_MESSAGE_RE.search(text_sample):
            signatures.append("english_message_line")

        if any(sig.startswith("english") for sig in signatures):
            english_count = sum(1 for sig in signatures if sig.startswith("english"))
            return DetectionResult(
                source=preprocessed.source,
                file_type=preprocessed.file_type,
                detected_format=EN_MOBILE_FORMAT,
                locale_guess="en",
                confidence=min(0.50 + (english_count * 0.16), 0.95),
                matched_signatures=[sig for sig in signatures if sig.startswith("english")],
                reason="Matched observed English mobile text export signatures.",
            )

    return DetectionResult(
        source=preprocessed.source,
        file_type=preprocessed.file_type,
        detected_format=UNKNOWN_FORMAT,
        locale_guess=_guess_locale(text_sample),
        confidence=0.0,
        matched_signatures=[],
        reason="No known KakaoTalk export format signatures matched.",
    )


def _detect_csv(preprocessed: PreprocessedInput, text_sample: str) -> DetectionResult:
    """Detect KakaoTalk PC CSV exports from CSV headers and row patterns."""

    signatures: list[str] = []
    try:
        # Use Python's CSV parser so quoted multiline messages do not confuse detection.
        reader = csv.reader(StringIO(preprocessed.text))
        header = next(reader, [])
    except csv.Error:
        header = []

    normalized_header = [col.strip().lower() for col in header]
    # PC CSV assumption: first row has exactly Date, User, Message columns.
    if normalized_header == ["date", "user", "message"]:
        signatures.append("csv_header_date_user_message")

    # PC CSV timestamp assumption: "2026-05-27 22:52:01".
    if re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", text_sample):
        signatures.append("csv_iso_timestamp")

    # Deleted-message rows are system messages in the inspected PC CSV export.
    if "The message has been deleted." in text_sample:
        signatures.append("csv_deleted_message_system_text")

    # Photo/Video are weak clues; they help locale guessing but are not enough alone.
    if "Photo" in text_sample or "Video" in text_sample:
        signatures.append("english_media_placeholder")

    locale = _guess_locale(text_sample)
    if len(signatures) >= 2 or "csv_header_date_user_message" in signatures:
        detected_format = PC_CSV_KO_FORMAT if locale == "ko" else PC_CSV_EN_FORMAT
        return DetectionResult(
            source=preprocessed.source,
            file_type=preprocessed.file_type,
            detected_format=detected_format,
            locale_guess=locale,
            confidence=min(0.60 + (len(signatures) * 0.12), 0.98),
            matched_signatures=signatures,
            reason="Matched KakaoTalk PC CSV export signatures.",
        )

    return DetectionResult(
        source=preprocessed.source,
        file_type=preprocessed.file_type,
        detected_format=UNKNOWN_FORMAT,
        locale_guess=locale,
        confidence=0.0,
        matched_signatures=signatures,
        reason="CSV file did not match known KakaoTalk PC export signatures.",
    )


def _guess_locale(text: str) -> str:
    """Return a best-effort locale clue; parser choice should not depend on this alone."""

    if HANGUL_RE.search(text) or "오전" in text or "오후" in text:
        return "ko"
    if re.search(r"\b(Date Saved|Photo|Video|The message has been deleted)\b", text, re.IGNORECASE):
        return "en"
    return "unknown"
