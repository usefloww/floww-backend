"""UUID generation utilities using ULID for time-ordered identifiers."""

from uuid import UUID

from ulid import ULID


def generate_ulid_uuid() -> UUID:
    """
    Generate a UUID from a ULID (Universally Unique Lexicographically Sortable Identifier).

    ULIDs are time-ordered, making them better for database ordering than standard UUIDs.
    This function generates a ULID and converts it to a UUID format compatible with PostgreSQL.

    Returns:
        A UUID generated from a ULID
    """
    ulid = ULID()
    # ULID is 128 bits, same as UUID
    # Convert ULID bytes to UUID
    return UUID(bytes=ulid.bytes)
