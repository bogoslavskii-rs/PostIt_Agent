from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return _b64encode(salt + derived)


def verify_password(password: str, password_hash: str) -> bool:
    raw = _b64decode(password_hash)
    salt, expected = raw[:16], raw[16:]
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(actual, expected)


def create_access_token(secret_key: str, subject: str, ttl_minutes: int) -> str:
    payload = {
        "sub": subject,
        "exp": int(time.time()) + ttl_minutes * 60,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = _b64encode(payload_bytes)
    signature = hmac.new(secret_key.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_part}.{_b64encode(signature)}"


def decode_access_token(secret_key: str, token: str) -> dict[str, Any]:
    try:
        payload_part, signature_part = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Malformed token") from exc

    expected = hmac.new(secret_key.encode("utf-8"), payload_part.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(signature_part)):
        raise ValueError("Invalid token signature")

    payload = json.loads(_b64decode(payload_part).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token expired")
    return payload

