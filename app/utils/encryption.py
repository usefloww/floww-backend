from cryptography.fernet import Fernet

from app.settings import settings


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
