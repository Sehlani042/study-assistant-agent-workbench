from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_VERSION = "v1"
_NONCE_BYTES = 16
_TAG_BYTES = 32


def _derive_key(secret: str) -> bytes:
    seed = (secret or "").encode("utf-8")
    return hashlib.sha256(seed).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt_secret(*, plaintext: str, secret: str) -> str:
    raw = str(plaintext or "").encode("utf-8")
    key = _derive_key(secret)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    stream = _keystream(key, nonce, len(raw))
    cipher = bytes(a ^ b for a, b in zip(raw, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    payload = nonce + cipher + tag
    encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    return f"{_VERSION}:{encoded}"


def decrypt_secret(*, ciphertext: str, secret: str) -> str:
    text = str(ciphertext or "").strip()
    if not text:
        return ""
    version, sep, body = text.partition(":")
    if sep != ":" or version != _VERSION:
        raise ValueError("unsupported ciphertext version")
    payload = base64.urlsafe_b64decode(body.encode("ascii"))
    if len(payload) < _NONCE_BYTES + _TAG_BYTES:
        raise ValueError("ciphertext too short")
    nonce = payload[:_NONCE_BYTES]
    tag = payload[-_TAG_BYTES:]
    cipher = payload[_NONCE_BYTES:-_TAG_BYTES]
    key = _derive_key(secret)
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("ciphertext authentication failed")
    stream = _keystream(key, nonce, len(cipher))
    raw = bytes(a ^ b for a, b in zip(cipher, stream))
    return raw.decode("utf-8")

