from models import PreprocessedInput, RawMessage


class BaseParser:
    """Shared parser interface so each format can stay small and focused."""

    format_id = "base"

    def can_parse(self, preprocessed: PreprocessedInput) -> bool:
        """Optional guard for future parser selection or tests."""
        return False

    def parse(self, preprocessed: PreprocessedInput) -> list[RawMessage]:
        """Extract raw messages from one already-detected export format."""
        raise NotImplementedError
