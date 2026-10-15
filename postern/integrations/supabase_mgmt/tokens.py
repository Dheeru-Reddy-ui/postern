"""Envelope encryption for OAuth tokens at rest (TM-05).

Format: b"pst1" || 12-byte nonce || AES-256-GCM ciphertext+tag. The key comes from Secret
Manager (token-encryption-key) through the environment, only into postern-api; the database
never holds it. The "pst1" header is what the connections table's check constraint requires,
so plaintext can't be stored by mistake.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEADER = b"pst1"
KEY_VERSION = "v1"
_AAD = b"postern-oauth-token-v1"


class TokenCipher:
    def __init__(self, key: bytes, key_version: str = KEY_VERSION) -> None:
        if len(key) != 32:
            raise ValueError("token key must be 32 bytes")
        self._aead = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_env(cls, name: str = "POSTERN_TOKEN_KEY") -> "TokenCipher":
        encoded = os.environ.get(name)
        if not encoded:
            raise RuntimeError(f"{name} is not set")
        return cls(base64.b64decode(encoded))

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        return HEADER + nonce + self._aead.encrypt(nonce, plaintext.encode("utf-8"), _AAD)

    def decrypt(self, envelope: bytes) -> str:
        if envelope[:4] != HEADER:
            raise ValueError("not a Postern token envelope")
        nonce, ciphertext = envelope[4:16], envelope[16:]
        return self._aead.decrypt(nonce, ciphertext, _AAD).decode("utf-8")
