"""boce.com 拦截检测接口：微信、QQ、DNS 污染、被墙拦截、黑名单。"""

from __future__ import annotations

from typing import Any

import aiohttp

from . import db
from .crypto import decrypt_cookie, encrypt_cookie

API_BASE = "https://api.boce.com/v3/task/create"
SECRET_NAME = "boce_api_key"

# 检测项 -> (展示名, 接口路径, 结果码是否命中, 可重试的结果码)
CHECKS: dict[str, tuple[str, str, dict[int, bool], set[int]]] = {
    "wechat": ("微信拦截", "wechat", {1: False, 2: True}, {3}),
    "qq": ("QQ 拦截", "qq", {1: False, 2: True}, {3}),
    # -1 表示域名未注册，无法被污染，按正常处理
    "pollution": ("污染", "pollute", {-1: False, 0: False, 1: True}, set()),
    # 2 为疑似被墙，按命中处理
    "blocked": ("拦截", "wall", {0: False, 1: True, 2: True}, {3}),
    "blacklist": ("黑名单", "blacklist", {1: False, 2: True}, set()),
}
STATUS_COLUMNS = {
    "wechat": "wechat_status",
    "qq": "qq_status",
    "pollution": "pollution_status",
    "blocked": "blocked_status",
    "blacklist": "blacklist_status",
}
RESULT_TEXTS = {
    "wechat": {1: "正常", 2: "封禁", 3: "检测失败"},
    "qq": {1: "正常", 2: "拦截", 3: "检测失败"},
    "pollution": {-1: "未注册", 0: "正常", 1: "污染"},
    "blocked": {0: "正常", 1: "被墙", 2: "疑似被墙", 3: "无资源", 4: "域名格式错误"},
    "blacklist": {1: "正常", 2: "在黑名单"},
}
ERROR_TEXTS = {-1: "节点异常", 1: "鉴权失败", 2: "参数错误", 3: "波点不足或未配置", 4: "任务 ID 失败或失效"}
FATAL_ERROR_CODES = {1, 3}
RETRYABLE_ERROR_CODES = {-1, 4}


class BoceError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class BoceFatalError(BoceError):
    """鉴权失败、波点不足等，继续查询也不会成功，需要终止任务。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


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


class BoceClient:
    def __init__(self, api_key: str, timeout: float = 30) -> None:
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    @staticmethod
    def _proxy(proxy: str | None) -> str | None:
        if not proxy:
            return None
        return proxy if "://" in proxy else f"http://{proxy}"

    async def check(self, item: str, domain: str, proxy: str | None = None) -> tuple[bool, str]:
        """返回（是否命中，结果描述）。"""
        _, path, hits, retry_codes = CHECKS[item]
        host = domain.strip().lower()
        try:
            host = host.encode("idna").decode()
        except UnicodeError:
            pass
        async with aiohttp.ClientSession(timeout=self.timeout, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(
                f"{API_BASE}/{path}", params={"key": self.api_key, "host": host}, proxy=self._proxy(proxy)
            ) as response:
                if response.status >= 400:
                    raise BoceError(f"接口返回 HTTP {response.status}")
                body: dict[str, Any] = await response.json(content_type=None)
        code = int(body.get("error_code", -1))
        if code != 0:
            message = body.get("error") or ERROR_TEXTS.get(code, "未知错误")
            if code in FATAL_ERROR_CODES:
                raise BoceFatalError(f"拦截检测接口{ERROR_TEXTS[code]}：{message}")
            raise BoceError(f"接口错误 {code}：{message}", retryable=code in RETRYABLE_ERROR_CODES)
        value = self._pick(body.get("data"), {host, domain.strip().lower()})
        if value is None:
            raise BoceError("接口未返回该域名的检测结果")
        text = RESULT_TEXTS[item].get(value, f"未知结果 {value}")
        if value in retry_codes:
            raise BoceError(text)
        if value not in hits:
            raise BoceError(text, retryable=False)
        return hits[value], text

    @staticmethod
    def _pick(data: Any, names: set[str]) -> int | None:
        if isinstance(data, list):
            data = {k: v for entry in data if isinstance(entry, dict) for k, v in entry.items()}
        if not isinstance(data, dict) or not data:
            return None
        for key, value in data.items():
            if str(key).lower().removeprefix("www.") in {name.removeprefix("www.") for name in names}:
                return int(value)
        # 单域名查询时接口返回的键名可能与请求不一致（如自动补 www）
        return int(next(iter(data.values()))) if len(data) == 1 else None
