"""Pairing + JWT auth.

Flow:
  1. Agent prints a QR code containing the URL plus the pairing secret.
  2. Phone opens the URL; the embedded secret is exchanged for a long-lived JWT
     via POST /api/pair.
  3. Every WebSocket / REST call carries that JWT (header or query param).

The pairing secret never expires on its own but can be rotated from the agent
console, which invalidates previously paired devices on next token refresh.
"""
from __future__ import annotations

import time

import jwt

from .config import config

ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 90  # 90 days


def pairing_secret() -> str:
    return config.get("pairing_secret")


def verify_pairing_secret(candidate: str) -> bool:
    expected = pairing_secret()
    # Constant-time-ish comparison.
    if not candidate or len(candidate) != len(expected):
        return False
    result = 0
    for a, b in zip(candidate, expected):
        result |= ord(a) ^ ord(b)
    return result == 0


def issue_token(device_label: str = "phone") -> str:
    now = int(time.time())
    payload = {
        "sub": device_label,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "scope": "agent",
    }
    return jwt.encode(payload, config.get("jwt_secret"), algorithm=ALGORITHM)


def verify_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return jwt.decode(token, config.get("jwt_secret"), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
