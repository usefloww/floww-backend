"""Password hashing and validation utilities for password-based authentication."""

import re
from uuid import UUID

import argon2
from argon2.exceptions import VerifyMismatchError


def hash_password(password: str, user_id: UUID) -> str:
    """
    Hash a password using Argon2 with user_id as salt.

    Args:
        password: The plaintext password to hash
        user_id: The user's UUID to use as salt

    Returns:
        The hashed password as a string

    Note:
        Using user_id as salt is deterministic but acceptable since:
        1. UUIDs are unique per user
        2. Password entropy should be high due to validation
        3. Argon2 adds additional computational complexity
    """
    hasher = argon2.PasswordHasher()
    # Convert UUID to bytes for salt
    salt = str(user_id).encode("utf-8")
    return hasher.hash(password, salt=salt)


def verify_password(password: str, password_hash: str, user_id: UUID) -> bool:
    """
    Verify a password against its hash using Argon2.

    Args:
        password: The plaintext password to verify
        password_hash: The stored hashed password
        user_id: The user's UUID (used as salt)

    Returns:
        True if password matches, False otherwise
    """
    hasher = argon2.PasswordHasher()
    try:
        # Argon2's verify will raise an exception if password doesn't match
        hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        # Catch any other exceptions (invalid hash format, etc.)
        return False


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password meets minimum security requirements.

    Requirements:
    - At least 8 characters
    - At most 128 characters
    - Contains at least one uppercase letter
    - Contains at least one lowercase letter
    - Contains at least one number
    - Contains at least one special character

    Args:
        password: The password to validate

    Returns:
        Tuple of (is_valid, error_message)
        If valid, error_message will be empty string
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"

    if len(password) > 128:
        return False, "Password must be at most 128 characters long"

    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"

    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"

    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least one number"

    if not re.search(r'[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\;/`~]', password):
        return False, "Password must contain at least one special character"

    return True, ""
