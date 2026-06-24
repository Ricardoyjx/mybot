from typing import Any, Mapping

INTERNAL_CONTINUATION_PENDING_META = "_internal_continuation_pending"


def internal_continuation_pending(metadata: Mapping[str, Any] | None) -> bool:
    """True when the current turn scheduled an invisible continuation slice."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_PENDING_META) is True)
