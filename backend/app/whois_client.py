from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

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

RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
RDAP_BOOTSTRAP_TTL = 24 * 60 * 60
RDAP_BOOTSTRAP_RETRY_DELAY = 60
RDAP_TIMEOUT_SECONDS = 10
DOMAIN_TIMEZONE = timezone(timedelta(hours=8))


class RdapError(RuntimeError):
    """RDAP did not provide a usable answer."""

    proxy_ok = False


class RdapIncompleteError(RdapError):
    """RDAP returned an object without the fields needed by the query."""

    proxy_ok = True


class _RdapHTTPError(RdapError):
    proxy_ok = True

    def __init__(self, status: int) -> None:
        super().__init__(f"RDAP HTTP {status}")
        self.status = status


_rdap_bootstrap: dict[str, list[str]] = {}
_rdap_bootstrap_loaded_at = 0.0
_rdap_bootstrap_lock = threading.Lock()
_rdap_bootstrap_failures: dict[str, tuple[float, RdapError]] = {}


def _proxy_url(proxy: str | None) -> str | None:
    if not proxy:
        return None
    return proxy if "://" in proxy else f"http://{proxy}"


def _http_json(url: str, proxy: str | None) -> tuple[dict[str, Any], str]:
    proxy_url = _proxy_url(proxy)
    try:
        handlers = ProxyHandler({"http": proxy_url, "https": proxy_url} if proxy_url else {})
    except (TypeError, ValueError) as exc:
        raise RdapError("RDAP 代理格式无效") from exc
    request = Request(
        url,
        headers={
            "Accept": "application/rdap+json, application/json",
            "User-Agent": "domain-query/1.0",
        },
    )
    try:
        with build_opener(handlers).open(request, timeout=RDAP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise _RdapHTTPError(exc.code) from exc
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        raise RdapError(f"RDAP 请求失败：{exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RdapError("RDAP 返回格式无效") from exc
    if not isinstance(payload, dict):
        raise RdapError("RDAP 返回格式无效")
    return payload, raw


def _load_rdap_bootstrap(proxy: str | None) -> dict[str, list[str]]:
    payload, _ = _http_json(RDAP_BOOTSTRAP_URL, proxy)
    services: dict[str, list[str]] = {}
    for item in payload.get("services") or []:
        if not isinstance(item, list) or len(item) != 2:
            continue
        tlds, bases = item
        if not isinstance(tlds, list) or not isinstance(bases, list):
            continue
        valid_bases = [str(base).strip().rstrip("/") for base in bases if str(base).strip()]
        for tld in tlds:
            key = str(tld).strip().lower().lstrip(".")
            if key and valid_bases:
                services.setdefault(key, []).extend(valid_bases)
    for key, values in services.items():
        services[key] = list(dict.fromkeys(values))
    if not services:
        raise RdapError("RDAP Bootstrap 未返回服务地址")
    return services


def _rdap_urls(domain: str, proxy: str | None) -> tuple[str, list[str]]:
    global _rdap_bootstrap, _rdap_bootstrap_loaded_at
    normalized = str(domain).strip().lower().rstrip(".")
    try:
        ascii_domain = normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RdapError("域名 IDNA 格式无效") from exc
    tld = ascii_domain.rsplit(".", 1)[-1] if "." in ascii_domain else ""
    now = time.monotonic()
    if not _rdap_bootstrap or now - _rdap_bootstrap_loaded_at >= RDAP_BOOTSTRAP_TTL:
        proxy_key = _proxy_url(proxy) or ""
        failure = _rdap_bootstrap_failures.get(proxy_key)
        if failure and now < failure[0]:
            raise failure[1]
        with _rdap_bootstrap_lock:
            now = time.monotonic()
            if not _rdap_bootstrap or now - _rdap_bootstrap_loaded_at >= RDAP_BOOTSTRAP_TTL:
                failure = _rdap_bootstrap_failures.get(proxy_key)
                if failure and now < failure[0]:
                    raise failure[1]
                try:
                    _rdap_bootstrap = _load_rdap_bootstrap(proxy)
                except RdapError as exc:
                    _rdap_bootstrap_failures[proxy_key] = (now + RDAP_BOOTSTRAP_RETRY_DELAY, exc)
                    raise
                _rdap_bootstrap_loaded_at = now
                _rdap_bootstrap_failures.pop(proxy_key, None)
    bases = _rdap_bootstrap.get(tld, [])
    if not bases:
        raise RdapError(f"未找到 .{tld} 的 RDAP 服务")
    return ascii_domain, bases


def _rdap_result(domain: str, payload: dict[str, Any], raw: str) -> dict[str, Any]:
    events = payload.get("events") or []
    dates: dict[str, date | datetime] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction") or "").strip().lower()
        parsed = _date_value(event.get("eventDate"))
        if parsed and action:
            dates[action] = parsed
    statuses = _text_list(payload.get("status"))
    expiration = dates.get("expiration") or dates.get("expiry")
    creation = dates.get("registration") or dates.get("creation")
    if not expiration or not statuses:
        raise RdapIncompleteError("RDAP 未返回完整的到期时间或域名状态")
    return {
        "domain": str(payload.get("ldhName") or domain),
        "expiration_date": expiration.isoformat(),
        "creation_date": creation.isoformat() if creation else "",
        "status": " | ".join(statuses),
        "statuses": statuses,
        "registrar": "",
        "name_servers": [],
        "raw": raw,
        "available": False,
        "source": "rdap",
    }


def _lookup_rdap(domain: str, proxy: str | None = None) -> dict[str, Any]:
    ascii_domain, bases = _rdap_urls(domain, proxy)
    encoded_domain = quote(ascii_domain, safe=".")
    last_error: Exception | None = None
    for base in bases:
        try:
            payload, raw = _http_json(f"{base}/domain/{encoded_domain}", proxy)
            return _rdap_result(domain, payload, raw)
        except _RdapHTTPError as exc:
            last_error = exc
        except RdapError as exc:
            last_error = exc
    raise last_error or RdapError("RDAP 查询失败")


def _date_value(value: Any) -> date | datetime | None:
    """保留来源精度；有时区的时间转为北京时间，无时区的保留原始时间。"""
    if isinstance(value, datetime):
        return value.astimezone(DOMAIN_TIMEZONE) if value.tzinfo else value
    if isinstance(value, date):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = _date_value(item)
            if parsed:
                return parsed
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00").replace("/", "-")
        for parser in (date.fromisoformat, datetime.fromisoformat):
            try:
                return _date_value(parser(text))
            except ValueError:
                pass
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed if "%H" in fmt else parsed.date()
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
        return "已过期" if date.fromisoformat(expiration_text[:10]) <= datetime.now(DOMAIN_TIMEZONE).date() else "未过期"
    except ValueError:
        return ""


def lookup(domain: str, proxy: str | None = None) -> dict[str, Any]:
    """Use proxied RDAP first, then direct python-whois as a fallback."""
    rdap_proxy_ok = False
    rdap_error = ""
    try:
        return _lookup_rdap(domain, proxy)
    except RdapError as exc:
        rdap_proxy_ok = exc.proxy_ok
        rdap_error = str(exc)
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
            "source": "whois",
            "_rdap_proxy_ok": rdap_proxy_ok,
            "_rdap_error": rdap_error,
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
        "source": "whois",
        "_rdap_proxy_ok": rdap_proxy_ok,
        "_rdap_error": rdap_error,
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
        expiration = date.fromisoformat(expiration_text[:10]) if expiration_text else None
    except ValueError:
        expiration = None
    try:
        creation = date.fromisoformat(creation_text[:10]) if creation_text else None
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
