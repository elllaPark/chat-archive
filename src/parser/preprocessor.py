from pathlib import Path

from models import PreprocessedInput


# KakaoTalk exports can come from Korean or English app/device settings.
# These encodings cover the samples seen so far plus common Korean text exports.
SUPPORTED_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")


def preprocess_file(file_path: str | Path, sample_size: int = 30) -> PreprocessedInput:
    """Read an export file safely and prepare shared input for detection/parsing."""

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    # Read bytes first so we can try several encodings without losing data.
    data = path.read_bytes()
    last_error: UnicodeDecodeError | None = None
    encoding_used = ""
    raw_text = ""

    for encoding in SUPPORTED_ENCODINGS:
        try:
            raw_text = data.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError as exc:
            last_error = exc

    if not encoding_used:
        # If every known encoding fails, stop here; guessing could corrupt chat data.
        raise UnicodeDecodeError(
            last_error.encoding if last_error else "unknown",
            last_error.object if last_error else data,
            last_error.start if last_error else 0,
            last_error.end if last_error else 1,
            "Unable to decode KakaoTalk export with supported encodings",
        )

    # Parsers should not need to care whether a file used Windows or Unix endings.
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    # utf-8-sig removes BOM during decoding, but this keeps the output clean if a
    # BOM-like marker survives through another encoding path.
    normalized = normalized.lstrip("\ufeff")
    # Format detection should use only a small sample to avoid scanning huge files.
    sample_lines = normalized.split("\n")[:sample_size]

    suffix = path.suffix.lower().lstrip(".")
    return PreprocessedInput(
        source=str(path),
        file_type=suffix or "unknown",
        encoding=encoding_used,
        raw_text=raw_text,
        text=normalized,
        sample_lines=sample_lines,
    )
