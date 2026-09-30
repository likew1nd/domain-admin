from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


BASE_DIR = Path(__file__).resolve().parents[2]
KEY_PATH = Path(os.getenv("COOKIE_KEY_PATH", str(BASE_DIR / "data" / ".cookie.key")))


def _get_fernet() -> Fernet:
    configured_key = os.getenv("COOKIE_ENCRYPTION_KEY", "").strip()
    if configured_key:
        return Fernet(configured_key.encode())

    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
    return Fernet(KEY_PATH.read_bytes().strip())


def encrypt_cookie(value: str) -> str:
    if not value:
        return ""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_cookie(value: str) -> str:
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Cookie encryption key is invalid") from exc
