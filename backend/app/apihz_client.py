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


VIP_BASE_URL = "https://vip.apihz.cn/api/wangzhan"
RDAP_API_URL = f"{VIP_BASE_URL}/whoisrdap.php"
WHOIS_API_URL = f"{VIP_BASE_URL}/whoisall.php"
# 保留旧名称供已有调用方兼容；实际查询统一走 VIP 线路。
API_URL = WHOIS_API_URL
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


def _interface_name(endpoint: str) -> str:
    return "RDAP" if endpoint == RDAP_API_URL else "WHOIS"


def _interface_error(interface: str, message: str) -> ApiHzError:
    return ApiHzError(f"接口盒子（{interface}）{message}")


def _request(
    domain: str,
    api_id: str,
    api_key: str,
    endpoint: str = API_URL,
) -> tuple[dict[str, Any], str]:
    interface = _interface_name(endpoint)
    query = urlencode({"id": api_id, "key": api_key, "domain": domain, "type": "2"})
    request = Request(
        f"{endpoint}?{query}",
        headers={
            "Accept": "application/rdap+json, application/json",
            "User-Agent": "domain-query/1.0",
        },
    )
    try:
        with build_opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise _interface_error(interface, f"HTTP {exc.code}") from exc
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise _interface_error(interface, f"请求失败：{exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _interface_error(interface, "返回格式无效") from exc
    if isinstance(payload, (int, float)) and int(payload) == payload:
        return {"code": int(payload), "msg": f"接口盒子返回错误码 {int(payload)}"}, raw
    if isinstance(payload, str) and payload.strip().isdigit():
        code = int(payload.strip())
        return {"code": code, "msg": f"接口盒子返回错误码 {code}"}, raw
    if not isinstance(payload, dict):
        raise _interface_error(interface, "返回格式无效")
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


def _api_code(payload: dict[str, Any]) -> int | None:
    value = payload.get("code")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _available_result(domain: str, raw: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "expiration_date": "",
        "creation_date": "",
        "status": "",
        "statuses": [],
        "registrar": "",
        "name_servers": [],
        "raw": raw,
        "available": True,
        "source": "apihz",
    }


def _parse_whois(
    domain: str,
    payload: dict[str, Any],
    raw_response: str,
    interface: str = "WHOIS",
) -> dict[str, Any]:
    code = _api_code(payload)
    if code != 200:
        if code == 404:
            return _available_result(domain, str(payload.get("msg") or raw_response))
        raise _interface_error(interface, str(payload.get("msg") or f"返回错误码 {code or 0}"))

    raw = _normalise_whois(payload.get("whois") or payload.get("data"))
    if not raw.strip():
        raise _interface_error(interface, "未返回 WHOIS 信息")
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
            "no data found",
            "not found",
            "not registered",
            "domain available",
            "可注册",
            "未注册",
        )
    )
    if not expiration and not statuses and not available:
        raise _interface_error(interface, "返回信息不足，无法确定域名状态")
    return {
        "domain": domain,
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


def _parse_rdap(domain: str, payload: dict[str, Any], raw_response: str) -> dict[str, Any]:
    code = _api_code(payload)
    # 成功的 RDAP 响应会在对象字段之外附加 code=200/cachetime，不能按错误包装处理。
    if payload.get("objectClassName") == "domain":
        code = None
    if code is not None:
        if code == 404:
            return _available_result(domain, str(payload.get("msg") or raw_response))
        raise _interface_error("RDAP", str(payload.get("msg") or f"返回错误码 {code or 0}"))
    if payload.get("objectClassName") != "domain":
        raise _interface_error("RDAP", "返回信息不足，无法确定域名状态")

    dates: dict[str, date] = {}
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_date = _date_value(str(event.get("eventDate") or ""))
        action = str(event.get("eventAction") or "").strip().lower()
        if event_date and action:
            dates[action] = event_date
    statuses = [str(item).strip() for item in payload.get("status") or [] if str(item).strip()]
    expiration = dates.get("expiration") or dates.get("expiry")
    creation = dates.get("registration") or dates.get("creation")
    if not expiration and not statuses:
        raise _interface_error("RDAP", "返回信息不足，无法确定域名状态")
    return {
        "domain": str(payload.get("ldhName") or domain),
        "expiration_date": expiration.isoformat() if expiration else "",
        "creation_date": creation.isoformat() if creation else "",
        "status": " | ".join(statuses),
        "statuses": statuses,
        "registrar": "",
        "name_servers": [],
        "raw": raw_response,
        "available": False,
        "source": "apihz",
    }


def lookup(domain: str, api_id: str, api_key: str | None = None) -> dict[str, Any]:
    api_id = str(api_id or "").strip()
    api_key = str(api_key if api_key is not None else get_api_key()).strip()
    if not api_id or not api_key:
        raise ApiHzError("接口盒子未配置 ID 或 KEY")

    normalized = str(domain).strip().lower().rstrip(".")
    # 始终使用 VIP 线路和 type=2；RDAP 返回 400/403 时回退 WHOIS。
    payload, raw_response = _request(normalized, api_id, api_key, RDAP_API_URL)
    if _api_code(payload) in {400, 403}:
        payload, raw_response = _request(normalized, api_id, api_key, WHOIS_API_URL)
        return _parse_whois(normalized, payload, raw_response, "WHOIS")
    # 兼容旧测试/旧服务直接返回 WHOIS 包装对象的情况。
    if "whois" in payload or "data" in payload:
        return _parse_whois(normalized, payload, raw_response, "RDAP")
    return _parse_rdap(normalized, payload, raw_response)
