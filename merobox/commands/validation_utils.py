"""
Shared validation utilities for merobox commands.
"""

from merobox.commands.constants import PROTOCOL_NEAR


def validate_near_only_protocol(protocol: str, context: str = "") -> str:
    """Validate protocol is NEAR-only; return normalized protocol string.

    Args:
        protocol: The protocol value to validate.
        context: Optional prefix for error messages (e.g. "Step 'my_step'").

    Returns:
        Normalized protocol string (lowercase, stripped).

    Raises:
        ValueError: If protocol is invalid or not NEAR.
    """
    prefix = f"{context}: " if context else ""
    if not isinstance(protocol, str):
        raise ValueError(f"{prefix}'protocol' must be a string")
    normalized = protocol.strip().lower()
    if normalized != PROTOCOL_NEAR:
        raise ValueError(
            f"{prefix}unsupported protocol '{protocol}'. "
            f"Only '{PROTOCOL_NEAR}' is supported."
        )
    return normalized
