from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class RegistrarError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisterResult:
    success: bool
    status_code: int
    response: str


@dataclass(frozen=True)
class AvailabilityResult:
    """结果为 None 表示接口没有返回可确认的状态，需要下一轮重试。"""

    available: bool | None
    status_code: int
    response: str
    reason: str = ""


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise RegistrarError("平台配置不是有效 JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise RegistrarError("平台配置必须是 JSON 对象")


def _split_token(token: str, label: str) -> tuple[str, str]:
    first, separator, second = token.partition(":")
    if not separator or not first or not second:
        raise RegistrarError(f"{label} Token 格式应为：ID:Secret")
    return first.strip(), second.strip()


def _generic_success(status_code: int, response: str) -> bool:
    if not 200 <= status_code < 300:
        return False
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        if "success" in payload:
            return bool(payload["success"])
        if payload.get("error") or payload.get("errors") or payload.get("code") in {"Error", "error"}:
            return False
    text = response.lower()
    if any(marker in text for marker in ("error", "failed", "invalid", "insufficient")):
        return False
    return True


def _availability_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "available", "free", "可注册", "未注册"}:
        return True
    if text in {"false", "0", "no", "n", "unavailable", "taken", "registered", "不可注册", "已注册"}:
        return False
    return None


def _find_availability(payload: Any) -> bool | None:
    keys = {
        "available",
        "avail",
        "isavailable",
        "is_available",
        "domainavailable",
        "canregister",
        "can_register",
        "free",
        "isfree",
    }
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).replace("-", "").replace(" ", "").lower()
            if normalized in keys:
                result = _availability_value(value)
                if result is not None:
                    return result
            result = _find_availability(value)
            if result is not None:
                return result
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            result = _find_availability(value)
            if result is not None:
                return result
    return None


def _availability_result(status_code: int, response: str) -> AvailabilityResult:
    if not 200 <= status_code < 300:
        return AvailabilityResult(None, status_code, response, f"HTTP {status_code}")
    try:
        payload = json.loads(response)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and any(payload.get(key) for key in ("error", "errors", "Error", "ErrorMessage")):
        return AvailabilityResult(None, status_code, response, "接口返回错误")
    if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0", "OK", "Success", "success"):
        return AvailabilityResult(None, status_code, response, f"接口返回错误码 {payload['code']}")
    result = _find_availability(payload)
    if result is not None:
        return AvailabilityResult(result, status_code, response)
    text = response.lower()
    xml_match = re.search(
        r"<(?:available|avail|isavailable|domainavailable)\s*>\s*(true|false|yes|no|1|0|available|unavailable)\s*</",
        text,
    )
    if xml_match:
        result = _availability_value(xml_match.group(1))
        if result is not None:
            return AvailabilityResult(result, status_code, response)
    return AvailabilityResult(None, status_code, response, "接口未返回明确的可注册字段")


class RegistrarAdapter:
    def __init__(self, config: dict[str, Any], token: str) -> None:
        self.config = config
        self.token = token

    def register(self, domain: str) -> RegisterResult:
        raise NotImplementedError

    def test(self) -> tuple[bool, int, str]:
        raise NotImplementedError

    def check_available(self, domain: str) -> AvailabilityResult:
        raise RegistrarError("该注册商适配器不支持可注册状态查询")


class HttpJsonRegistrar(RegistrarAdapter):
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
        custom = _json_object(self.config.get("headers_json") or "{}")
        headers.update({str(key): str(value) for key, value in custom.items()})
        if self.token:
            headers.setdefault("Authorization", f"Bearer {self.token}")
        return headers

    def _request(self, method: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        endpoint = str(self.config.get("endpoint") or "").strip()
        if not endpoint.startswith(("http://", "https://")):
            raise RegistrarError("注册商 API 地址必须以 http:// 或 https:// 开头")
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        return _send(Request(endpoint, data=body, headers=self._headers(), method=method))

    def register(self, domain: str) -> RegisterResult:
        status_code, response = self._request("POST", {"domain": domain})
        return RegisterResult(_generic_success(status_code, response), status_code, response)

    def test(self) -> tuple[bool, int, str]:
        status_code, response = self._request("GET")
        return 200 <= status_code < 400, status_code, response


class DynadotRegistrar(RegistrarAdapter):
    endpoint = "https://api.dynadot.com/api3.html"

    def _request_command(self, command: str, domain: str = "") -> tuple[int, str]:
        config = _json_object(self.config.get("config_json", self.config.get("config", {})))
        endpoint = str(config.get("endpoint") or self.config.get("endpoint") or self.endpoint)
        params: dict[str, Any] = {"key": self.token, "command": command, "duration": str(config.get("duration", 1))}
        if domain:
            params["domain"] = domain
        extra = config.get("extra_params", {})
        if isinstance(extra, dict):
            params.update({str(key): str(value) for key, value in extra.items()})
        return _send(Request(f"{endpoint}?{urlencode(params)}", headers={"Accept": "application/xml, application/json"}))

    def register(self, domain: str) -> RegisterResult:
        status_code, response = self._request_command("register", domain)
        text = response.lower()
        success = _generic_success(status_code, response) and bool(
            re.search(r"(?:result|response)\s*code[^\d]*(?:0|success)", text) or "success" in text
        )
        return RegisterResult(success, status_code, response)

    def check_available(self, domain: str) -> AvailabilityResult:
        config = _json_object(self.config.get("config_json", self.config.get("config", {})))
        command = str(config.get("check_command", "search"))
        status_code, response = self._request_command(command, domain)
        return _availability_result(status_code, response)

    def test(self) -> tuple[bool, int, str]:
        status_code, response = self._request_command("account_info")
        return 200 <= status_code < 300 and "error" not in response.lower(), status_code, response


class GnameRegistrar(RegistrarAdapter):
    endpoint = "https://api.gname.com"

    def _request_signed(self, path: str, params: dict[str, Any]) -> tuple[int, str]:
        appid, appkey = _split_token(self.token, "GNAME")
        config = _json_object(self.config.get("config_json", self.config.get("config", {})))
        endpoint = str(config.get("endpoint") or self.config.get("endpoint") or self.endpoint).rstrip("/")
        params = {"appid": appid, "gntime": str(int(datetime.now(timezone.utc).timestamp())), **params}
        extra = config.get("extra_params", {})
        if isinstance(extra, dict):
            params.update(extra)
        signing = "&".join(f"{key}={quote(str(params[key]).strip(), safe='')}" for key in sorted(params))
        params["gntoken"] = hashlib.md5(f"{signing}{appkey}".encode()).hexdigest().upper()
        api_path = path if path.startswith("/") else f"/{path}"
        request = Request(
            f"{endpoint}{api_path}",
            data=urlencode(params).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        return _send(request)

    def register(self, domain: str) -> RegisterResult:
        config = _json_object(self.config.get("config_json", self.config.get("config", {})))
        params: dict[str, Any] = {"ym": domain}
        for key in ("mbid", "dns", "qian"):
            if config.get(key) not in (None, ""):
                params[key] = config[key]
        status_code, response = self._request_signed(str(config.get("register_path", "/domain/reg")), params)
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            payload = {}
        success = 200 <= status_code < 300 and isinstance(payload, dict) and int(payload.get("code", -1)) == 0
        return RegisterResult(success, status_code, response)

    def check_available(self, domain: str) -> AvailabilityResult:
        config = _json_object(self.config.get("config_json", self.config.get("config", {})))
        path = str(config.get("check_path", "/domain/check"))
        status_code, response = self._request_signed(path, {"ym": domain})
        return _availability_result(status_code, response)

    def test(self) -> tuple[bool, int, str]:
        config = _json_object(self.config.get("config_json", self.config.get("config", {})))
        status_code, response = self._request_signed(str(config.get("test_path", "/user/info")), {})
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            payload = {}
        return 200 <= status_code < 300 and isinstance(payload, dict) and int(payload.get("code", -1)) == 0, status_code, response


class GodaddyRegistrar(RegistrarAdapter):
    endpoint = "https://api.godaddy.com/v1/domains"

    def _config(self) -> dict[str, Any]:
        return _json_object(self.config.get("config_json", self.config.get("config", {})))

    def _auth_headers(self) -> dict[str, str]:
        api_key, api_secret = _split_token(self.token, "GoDaddy")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"sso-key {api_key}:{api_secret}",
        }

    def register(self, domain: str) -> RegisterResult:
        config = self._config()
        endpoint = str(config.get("endpoint") or self.config.get("endpoint") or self.endpoint).rstrip("/")
        payload = config.get("payload")
        if not isinstance(payload, dict):
            payload = {
                "period": int(config.get("period", 1)),
                "renewAuto": bool(config.get("renew_auto", False)),
                "privacy": bool(config.get("privacy", True)),
            }
            for key in ("consent", "contactAdmin", "contactBilling", "contactRegistrant", "contactTech"):
                if isinstance(config.get(key), dict):
                    payload[key] = config[key]
        status_code, response = _send(
            Request(
                f"{endpoint}/{domain}",
                data=json.dumps(payload, ensure_ascii=False).encode(),
                headers=self._auth_headers(),
                method="POST",
            )
        )
        return RegisterResult(_generic_success(status_code, response), status_code, response)

    def check_available(self, domain: str) -> AvailabilityResult:
        config = self._config()
        endpoint = str(config.get("endpoint") or self.config.get("endpoint") or self.endpoint).rstrip("/")
        status_code, response = _send(
            Request(f"{endpoint}/available/{quote(domain, safe='')}", headers=self._auth_headers(), method="GET")
        )
        return _availability_result(status_code, response)

    def test(self) -> tuple[bool, int, str]:
        config = self._config()
        endpoint = str(config.get("endpoint") or self.config.get("endpoint") or self.endpoint).rstrip("/")
        domain = str(config.get("test_domain", "example.com"))
        status_code, response = _send(
            Request(f"{endpoint}/available/{domain}", headers=self._auth_headers(), method="GET")
        )
        return 200 <= status_code < 300, status_code, response


class AliyunIntlRegistrar(RegistrarAdapter):
    endpoint = "https://domain-intl.aliyuncs.com/"

    def _config(self) -> dict[str, Any]:
        return _json_object(self.config.get("config_json", self.config.get("config", {})))

    def _request_action(self, action: str, domain: str = "") -> tuple[int, str]:
        access_key_id, access_key_secret = _split_token(self.token, "阿里云")
        config = self._config()
        endpoint = str(config.get("endpoint") or self.config.get("endpoint") or self.endpoint)
        params: dict[str, Any] = {
            "AccessKeyId": access_key_id,
            "Action": action,
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid4()),
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "Version": str(config.get("version", "2018-01-29")),
        }
        if domain:
            params["DomainName"] = domain
        for key in ("SubscriptionType", "Period", "RegistrantProfileId", "ContactId", "Lang"):
            if config.get(key) not in (None, ""):
                params[key] = config[key]
        canonical = "&".join(
            f"{quote(str(key), safe='-_.~')}={quote(str(params[key]), safe='-_.~')}" for key in sorted(params)
        )
        string_to_sign = f"GET&%2F&{quote(canonical, safe='-_.~')}"
        params["Signature"] = base64.b64encode(
            hmac.new(f"{access_key_secret}&".encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()
        return _send(Request(f"{endpoint}?{urlencode(params)}", headers={"Accept": "application/json"}, method="GET"))

    def register(self, domain: str) -> RegisterResult:
        config = self._config()
        status_code, response = self._request_action(str(config.get("register_action", "CreateOrder")), domain)
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            payload = {}
        success = 200 <= status_code < 300 and isinstance(payload, dict) and not payload.get("Code")
        return RegisterResult(success, status_code, response)

    def check_available(self, domain: str) -> AvailabilityResult:
        config = self._config()
        action = str(config.get("check_action", "CheckDomain"))
        status_code, response = self._request_action(action, domain)
        return _availability_result(status_code, response)

    def test(self) -> tuple[bool, int, str]:
        config = self._config()
        status_code, response = self._request_action(
            str(config.get("check_action", "CheckDomain")), str(config.get("test_domain", "example.com"))
        )
        return 200 <= status_code < 300 and "InvalidAccessKeyId" not in response, status_code, response


def _send(request: Request) -> tuple[int, str]:
    try:
        with urlopen(request, timeout=20) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")[:4000]
    except HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")[:4000]
    except URLError as exc:
        raise RegistrarError(f"注册商 API 网络错误: {exc.reason}") from exc


ADAPTERS = {
    "http_json": HttpJsonRegistrar,
    "aliyun_intl": AliyunIntlRegistrar,
    "dynadot": DynadotRegistrar,
    "gname": GnameRegistrar,
    "godaddy": GodaddyRegistrar,
}


def create_adapter(config: dict[str, Any], token: str) -> RegistrarAdapter:
    adapter_type = str(config.get("adapter") or "http_json")
    adapter = ADAPTERS.get(adapter_type)
    if adapter is None:
        raise RegistrarError(f"不支持的注册商适配器: {adapter_type}")
    return adapter(config, token)
