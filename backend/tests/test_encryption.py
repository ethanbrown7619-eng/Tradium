"""Tests for the encryption service."""
import pytest
from unittest.mock import patch


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        """Encrypting then decrypting should return the original key."""
        from app.services.encryption import encrypt_private_key, decrypt_private_key
        original = "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        encrypted = encrypt_private_key(original)
        assert encrypted != original
        decrypted = decrypt_private_key(encrypted)
        assert decrypted == original

    def test_encrypted_output_is_different_each_time(self):
        """Fernet uses a random IV, so same input should produce different ciphertext."""
        from app.services.encryption import encrypt_private_key
        key = "0x1234567890"
        enc1 = encrypt_private_key(key)
        enc2 = encrypt_private_key(key)
        assert enc1 != enc2

    def test_decrypt_with_wrong_data_raises(self):
        """Decrypting garbage should raise ValueError."""
        from app.services.encryption import decrypt_private_key
        with pytest.raises(ValueError, match="Failed to decrypt"):
            decrypt_private_key("not-valid-ciphertext")

    def test_private_key_never_in_encrypted(self):
        """The plaintext key should never appear in the ciphertext."""
        from app.services.encryption import encrypt_private_key
        key = "0xdeadbeef12345678"
        encrypted = encrypt_private_key(key)
        assert key not in encrypted
        assert "deadbeef" not in encrypted.lower()
