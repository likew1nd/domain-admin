from __future__ import annotations

from datetime import date, datetime
from typing import Any

import whois
from whois.exceptions import WhoisDomainNotFoundError


AVAILABLE_MARKERS = (
    "no match for",
    "not found",
    "no data found",
    "no matching record",
    "not registered",
    "is available",
    "domain available",
    "status: free",
    "未注册",
    "可注册",
    "无匹配",
)


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = _date_value(item)
            if parsed:
                return parsed
    if isinstance(value, str):
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


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _looks_available_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and any(marker in text for marker in AVAILABLE_MARKERS)


def is_available(info: dict[str, Any]) -> bool:
    if info.get("available") is True:
        return True
    if info.get("expiration_date") or info.get("creation_date"):
        return False
    statuses = info.get("statuses") or _text_list(info.get("status"))
    if statuses:
        return False
    return _looks_available_text(info.get("raw"))


def deletion_status(info: dict[str, Any]) -> str:
    if is_available(info):
        return "可注册"
    statuses = " ".join(_text_list(info.get("statuses") or info.get("status"))).lower().replace(" ", "")
    if "pendingdelete" in statuses:
        return "待删除"
    if "redemption" in statuses:
        return "赎回期"
    expiration_text = info.get("expiration_date", "")
    if not expiration_text:
        return ""
    try:
        return "已过期" if date.fromisoformat(expiration_text) <= date.today() else "未过期"
    except ValueError:
        return ""


def lookup(domain: str) -> dict[str, Any]:
    """Run the blocking python-whois call in a worker thread."""
    try:
        result = whois.whois(domain, inc_raw=True)
    except WhoisDomainNotFoundError as exc:
        raw = str(exc)
        if not _looks_available_text(raw):
            raise
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
        }
    expiration = _date_value(getattr(result, "expiration_date", None))
    creation = _date_value(getattr(result, "creation_date", None))
    statuses = _text_list(getattr(result, "status", None))
    raw = str(getattr(result, "raw", "") or str(result))
    return {
        "domain": domain,
        "expiration_date": expiration.isoformat() if expiration else "",
        "creation_date": creation.isoformat() if creation else "",
        "status": " | ".join(statuses),
        "statuses": statuses,
        "registrar": str(getattr(result, "registrar", "") or ""),
        "name_servers": _text_list(getattr(result, "name_servers", None)),
        "raw": raw,
        "available": not expiration and not creation and not statuses and _looks_available_text(raw),
    }


def match_filters(info: dict[str, Any], filters: dict[str, Any]) -> tuple[bool, str]:
    status = deletion_status(info)
    if status == "可注册":
        if any(
            filters.get(name)
            for name in ("expiration_start", "expiration_end", "registration_start", "registration_end")
        ):
            return False, "可注册域名没有可筛选的日期"
        return True, "WHOIS 显示域名可注册"
    expiration_text = info.get("expiration_date", "")
    creation_text = info.get("creation_date", "")
    try:
        expiration = date.fromisoformat(expiration_text) if expiration_text else None
    except ValueError:
        expiration = None
    try:
        creation = date.fromisoformat(creation_text) if creation_text else None
    except ValueError:
        creation = None

    delete_type = filters.get("delete_type", "expired")
    expected_status = {
        "expired": "已过期",
        "redemption": "赎回期",
        "pending_delete": "待删除",
    }.get(delete_type)
    if status == "未过期":
        return False, "尚未过期"
    if not status:
        return False, "WHOIS 未返回有效删除状态"
    if expected_status and status != expected_status:
        return False, f"不在{expected_status}"

    expiration_start = filters.get("expiration_start") or ""
    expiration_end = filters.get("expiration_end") or ""
    if expiration_start and (expiration is None or expiration < date.fromisoformat(expiration_start)):
        return False, "到期时间早于筛选范围"
    if expiration_end and (expiration is None or expiration > date.fromisoformat(expiration_end)):
        return False, "到期时间晚于筛选范围"

    registration_start = filters.get("registration_start") or ""
    registration_end = filters.get("registration_end") or ""
    if registration_start and (creation is None or creation < date.fromisoformat(registration_start)):
        return False, "注册时间早于筛选范围"
    if registration_end and (creation is None or creation > date.fromisoformat(registration_end)):
        return False, "注册时间晚于筛选范围"
    return True, "WHOIS 条件符合"
