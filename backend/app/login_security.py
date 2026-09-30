"""系统设置存储，以及登录相关的图形验证码、邮箱验证码和邮件发送。"""

from __future__ import annotations

import base64
import hmac
import io
import json
import random
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import db
from .crypto import decrypt_cookie, encrypt_cookie


DEFAULT_SETTINGS: dict[str, Any] = {
    "systemTitle": "域名抢注系统",
    "logo": "",
    "loginKeepDays": 7,
    "captchaEnabled": False,
    "lockMaxAttempts": 5,
    "lockMinutes": 15,
    "registerEnabled": False,
    "registerNeedApproval": True,
    "registerDefaultRole": "R_USER",
    "emailLoginEnabled": False,
    "passwordResetEnabled": False,
    "smtpHost": "",
    "smtpPort": 465,
    "smtpSsl": True,
    "smtpUser": "",
    "smtpPassword": "",
    "smtpFrom": "",
}
SECRET_KEYS = {"smtpPassword"}


def get_settings() -> dict[str, Any]:
    stored = {row["key"]: row["value"] for row in db.fetch_all("SELECT key, value FROM sys_settings")}
    settings = dict(DEFAULT_SETTINGS)
    for key, default in DEFAULT_SETTINGS.items():
        if key in stored:
            try:
                settings[key] = type(default)(json.loads(stored[key]))
            except (TypeError, ValueError):
                pass
    return settings


def email_configured(settings: dict[str, Any]) -> bool:
    return bool(settings["smtpHost"] and (settings["smtpFrom"] or settings["smtpUser"]))


def public_settings() -> dict[str, Any]:
    """登录前即可读取的配置，不能包含任何敏感信息。"""
    settings = get_settings()
    mail_ready = email_configured(settings)
    return {
        "systemTitle": settings["systemTitle"],
        "logo": settings["logo"],
        "captchaEnabled": settings["captchaEnabled"],
        "registerEnabled": settings["registerEnabled"],
        "registerEmailRequired": settings["registerEnabled"] and mail_ready,
        "emailLoginEnabled": settings["emailLoginEnabled"] and mail_ready,
        "passwordResetEnabled": settings["passwordResetEnabled"] and mail_ready,
    }


def admin_settings() -> dict[str, Any]:
    settings = get_settings()
    result = {key: value for key, value in settings.items() if key not in SECRET_KEYS}
    result["hasSmtpPassword"] = bool(settings["smtpPassword"])
    return result


def save_settings(values: dict[str, Any]) -> None:
    rows = []
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key in SECRET_KEYS:
            value = encrypt_cookie(value) if value else ""
        rows.append((key, json.dumps(value, ensure_ascii=False)))
    with db._db_lock, db.get_connection() as connection:
        connection.executemany(
            "INSERT INTO sys_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            rows,
        )


def send_mail(settings: dict[str, Any], to: str, subject: str, body: str) -> None:
    sender = settings["smtpFrom"] or settings["smtpUser"]
    password = decrypt_cookie(settings["smtpPassword"]) if settings["smtpPassword"] else ""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings["systemTitle"], sender))
    message["To"] = to
    message.set_content(body)

    context = ssl.create_default_context()
    host, port = settings["smtpHost"], int(settings["smtpPort"])
    client = (
        smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        if settings["smtpSsl"]
        else smtplib.SMTP(host, port, timeout=15)
    )
    with client:
        if not settings["smtpSsl"]:
            client.ehlo()
            if client.has_extn("starttls"):
                client.starttls(context=context)
                client.ehlo()
        if settings["smtpUser"]:
            client.login(settings["smtpUser"], password)
        client.send_message(message)


# ---------------------------------------------------------------- 验证码（仅保存在内存中，服务重启后失效）

CAPTCHA_TTL = 300
EMAIL_CODE_TTL = 300
EMAIL_RESEND_SECONDS = 60
EMAIL_MAX_ATTEMPTS = 5
_MAX_ENTRIES = 5000
_CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_FONT_PATHS = (
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

_lock = threading.Lock()
_captchas: dict[str, dict[str, Any]] = {}
_email_codes: dict[tuple[str, str], dict[str, Any]] = {}


def _prune(store: dict[Any, dict[str, Any]]) -> None:
    now = time.time()
    for key in [key for key, item in store.items() if item["expires"] <= now]:
        del store[key]
    while len(store) >= _MAX_ENTRIES:
        store.pop(next(iter(store)))


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _color(rng: random.Random, low: int, high: int) -> tuple[int, int, int]:
    return (rng.randint(low, high), rng.randint(low, high), rng.randint(low, high))


def create_captcha() -> dict[str, str]:
    code = "".join(secrets.choice(_CAPTCHA_CHARS) for _ in range(4))
    rng = random.Random()
    width, height = 120, 40
    image = Image.new("RGB", (width, height), (245, 247, 250))
    draw = ImageDraw.Draw(image)
    for _ in range(6):
        points = [(rng.randint(0, width), rng.randint(0, height)) for _ in range(2)]
        draw.line(points, fill=_color(rng, 150, 210), width=1)
    font = _font(26)
    for index, char in enumerate(code):
        glyph = Image.new("RGBA", (30, 36), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((4, 2), char, font=font, fill=_color(rng, 30, 120))
        glyph = glyph.rotate(rng.randint(-25, 25), resample=Image.BICUBIC)
        image.paste(glyph, (8 + index * 27, 2), glyph)
    for _ in range(80):
        draw.point((rng.randint(0, width - 1), rng.randint(0, height - 1)), fill=_color(rng, 100, 200))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    captcha_id = secrets.token_urlsafe(16)
    with _lock:
        _prune(_captchas)
        _captchas[captcha_id] = {"code": code, "expires": time.time() + CAPTCHA_TTL}
    return {"captchaId": captcha_id, "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()}


def verify_captcha(captcha_id: str, code: str) -> bool:
    """每个图形验证码只能校验一次，无论对错。"""
    with _lock:
        item = _captchas.pop(captcha_id or "", None)
    answer = (code or "").strip().upper()
    return bool(item and item["expires"] > time.time() and answer and hmac.compare_digest(item["code"], answer))


def issue_email_code(purpose: str, email: str) -> str | None:
    """生成新的邮箱验证码；距上次发送不足冷却时间时返回 None。"""
    key = (purpose, email.lower())
    now = time.time()
    with _lock:
        _prune(_email_codes)
        item = _email_codes.get(key)
        if item and now - item["sent_at"] < EMAIL_RESEND_SECONDS:
            return None
        code = f"{secrets.randbelow(1_000_000):06d}"
        _email_codes[key] = {"code": code, "expires": now + EMAIL_CODE_TTL, "sent_at": now, "attempts": 0}
    return code


def discard_email_code(purpose: str, email: str) -> None:
    with _lock:
        _email_codes.pop((purpose, email.lower()), None)


def verify_email_code(purpose: str, email: str, code: str) -> bool:
    key = (purpose, email.lower())
    with _lock:
        item = _email_codes.get(key)
        if not item or item["expires"] <= time.time():
            _email_codes.pop(key, None)
            return False
        if hmac.compare_digest(item["code"], (code or "").strip()):
            del _email_codes[key]
            return True
        item["attempts"] += 1
        if item["attempts"] >= EMAIL_MAX_ATTEMPTS:
            del _email_codes[key]
        return False
