"""接口盒子（APIHZ）域名 WHOIS 查询。"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from . import db
from .crypto import decrypt_cookie, encrypt_cookie


API_URL = "https://cn.apihz.cn/api/wangzhan/whoisall.php"
SECRET_NAME = "apihz_api_key"
TIMEOUT_SECONDS = 15


class ApiHzError(RuntimeError):
    """接口盒子没有返回可用的域名信息。"""


def get_api_key() -> str:
    row = db.fetch_one("SELECT ciphertext FROM app_secrets WHERE name = ?", (SECRET_NAME,))
    return decrypt_cookie(row["ciphertext"]) if row else ""


def has_api_key() -> bool:
    return db.fetch_one("SELECT 1 FROM app_secrets WHERE name = ? AND ciphertext <> ''", (SECRET_NAME,)) is not None


def save_api_key(value: str) -> None:
    value = value.strip()
    if not value:
        db.execute("DELETE FROM app_secrets WHERE name = ?", (SECRET_NAME,))
        return
    db.execute(
        """
        INSERT INTO app_secrets (name, ciphertext, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET ciphertext = excluded.ciphertext, updated_at = excluded.updated_at
        """,
        (SECRET_NAME, encrypt_cookie(value), db.utc_now()),
    )


def _request(domain: str, api_id: str, api_key: str) -> tuple[dict[str, Any], str]:
    query = urlencode({"id": api_id, "key": api_key, "domain": domain, "type": "2"})
    request = Request(
        f"{API_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": "domain-query/1.0"},
    )
    try:
        with build_opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise ApiHzError(f"接口盒子 HTTP {exc.code}") from exc
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise ApiHzError(f"接口盒子请求失败：{exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiHzError("接口盒子返回格式无效") from exc
    if not isinstance(payload, dict):
        raise ApiHzError("接口盒子返回格式无效")
    return payload, raw


def _date_value(value: str) -> date | None:
    text = value.strip().replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            return parser(text).date()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def _field_date(text: str, labels: tuple[str, ...]) -> date | None:
    date_pattern = r"(\d{4}[-/]\d{2}[-/]\d{2}(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:?\d{2})?)?)"
    for line in text.splitlines():
        lower = line.lower()
        if any(label in lower for label in labels):
            match = re.search(date_pattern, line)
            if match:
                parsed = _date_value(match.group(1))
                if parsed:
                    return parsed
    return None


def _normalise_whois(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).replace("\r", "")


def lookup(domain: str, api_id: str, api_key: str | None = None) -> dict[str, Any]:
    api_id = str(api_id or "").strip()
    api_key = str(api_key if api_key is not None else get_api_key()).strip()
    if not api_id or not api_key:
        raise ApiHzError("接口盒子未配置 ID 或 KEY")

    normalized = str(domain).strip().lower().rstrip(".")
    payload, raw_response = _request(normalized, api_id, api_key)
    try:
        code = int(payload.get("code", 0))
    except (TypeError, ValueError):
        code = 0
    if code != 200:
        raise ApiHzError(str(payload.get("msg") or f"接口盒子返回错误码 {code}"))

    raw = _normalise_whois(payload.get("whois") or payload.get("data"))
    if not raw.strip():
        raise ApiHzError("接口盒子未返回 WHOIS 信息")
    expiration = _field_date(
        raw,
        (
            "registry expiry date",
            "registrar registration expiration date",
            "expiration date",
            "expiration time",
            "expiry date",
            "到期",
        ),
    )
    creation = _field_date(
        raw,
        ("creation date", "created date", "registration date", "registration time", "注册"),
    )
    statuses = list(dict.fromkeys(re.findall(r"(?im)^\s*(?:domain\s+)?status\s*:\s*([^\s<]+)", raw)))
    available = not expiration and not creation and not statuses and any(
        marker in raw.lower()
        for marker in (
            "no match for",
            "no matching record",
            "not found",
            "not registered",
            "domain available",
            "可注册",
            "未注册",
        )
    )
    if not expiration and not statuses and not available:
        raise ApiHzError("接口盒子返回信息不足，无法确定域名状态")
    return {
        "domain": normalized,
        "expiration_date": expiration.isoformat() if expiration else "",
        "creation_date": creation.isoformat() if creation else "",
        "status": " | ".join(statuses),
        "statuses": statuses,
        "registrar": "",
        "name_servers": [],
        "raw": raw or raw_response,
        "available": available,
        "source": "apihz",
    }
