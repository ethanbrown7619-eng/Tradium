"""
Encryption service for wallet private keys.
Uses Fernet symmetric encryption (AES-128-CBC under the hood).
Keys are encrypted at rest and never logged.
"""
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.encryption_key.encode()
    # Derive a proper 32-byte key from the config value
    derived = hashlib.sha256(key).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_private_key(private_key: str) -> str:
    """Encrypt a wallet private key. Returns base64-encoded ciphertext."""
    fernet = _get_fernet()
    return fernet.encrypt(private_key.encode()).decode()


def decrypt_private_key(encrypted_key: str) -> str:
    """Decrypt a wallet private key. Raises ValueError on failure."""
    fernet = _get_fernet()
    try:
        return fernet.decrypt(encrypted_key.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt private key — encryption key may have changed")
