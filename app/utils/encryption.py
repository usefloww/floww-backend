import secrets
import string

import argon2
from cryptography.fernet import Fernet

from app.settings import settings

_completely_irrelevant_salt = (
    b"completely irrelevant salt because the entropy of the api key is high enough"
)


def encrypt_secret(value: str) -> str:
    """
    Encrypt a secret value using Fernet symmetric encryption.

    Args:
        value: The plaintext secret to encrypt

    Returns:
        The encrypted secret as a base64-encoded string
    """
    f = Fernet(settings.ENCRYPTION_KEY.encode())
    encrypted = f.encrypt(value.encode())
    return encrypted.decode()


def decrypt_secret(encrypted_value: str) -> str:
    """
    Decrypt a secret value using Fernet symmetric encryption.

    Args:
        encrypted_value: The encrypted secret as a base64-encoded string

    Returns:
        The decrypted plaintext secret
    """
    f = Fernet(settings.ENCRYPTION_KEY.encode())
    decrypted = f.decrypt(encrypted_value.encode())
    return decrypted.decode()


def generate_cryptographic_key(length: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_api_key(value: str) -> str:
    """No salt needed because entropy of api keys is high enough"""

    hasher = argon2.PasswordHasher()
    return hasher.hash(value, salt=_completely_irrelevant_salt)


def generate_api_key() -> tuple[str, str]:
    """Generate a random API key."""
    prepend_part = "floww_sa_"
    random_key = generate_cryptographic_key(32)
    prefix = f"{prepend_part}{random_key[:3]}"
    api_key = f"{prepend_part}{random_key}"

    return api_key, prefix
