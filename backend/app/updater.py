"""版本检查与在线更新。

当前版本来自镜像构建参数 APP_VERSION；最新版本读取 GitHub Releases。
在线更新不直接操作 Docker：后端调用 compose 中 watchtower 的 HTTP API，由它拉取新镜像
并按原配置重建 app 容器。后端随即被重启，因此是否成功以重启后的版本号是否变化来判断。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query

from . import db
from .system_manage import ApiError, SUPER_ROLE, current_user, ok

router = APIRouter(prefix="/api")

APP_VERSION = os.getenv("APP_VERSION", "dev").strip() or "dev"
APP_REPO = os.getenv("APP_REPO", "").strip()

WATCHTOWER_URL = os.getenv("WATCHTOWER_URL", "").strip().rstrip("/")
WATCHTOWER_TOKEN = os.getenv("WATCHTOWER_TOKEN", "").strip()

# 记录更新发起时的版本，重启后版本不同即为更新成功
STATUS_FILE = db.DB_PATH.parent / ".update-status"

# 超过该时长版本仍未变化视为更新失败，允许重新发起
_RUNNING_TIMEOUT = 300
_RELEASE_TTL = 600.0
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_release_cache: tuple[float, dict[str, Any]] | None = None
_release_lock = threading.Lock()


def _parse_version(value: str) -> tuple[int, ...] | None:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def _latest_release(refresh: bool) -> dict[str, Any]:
    global _release_cache
    with _release_lock:
        if not refresh and _release_cache and time.monotonic() - _release_cache[0] < _RELEASE_TTL:
            return _release_cache[1]
    if not APP_REPO:
        return {"error": "未配置 APP_REPO，无法检查更新"}
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{APP_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
            follow_redirects=True,
        )
        if response.status_code == 404:
            result: dict[str, Any] = {"error": "尚未发布任何版本"}
        else:
            response.raise_for_status()
            body = response.json()
            result = {
                "version": body.get("tag_name", ""),
                "notes": body.get("body") or "",
                "url": body.get("html_url", ""),
                "publishedAt": body.get("published_at", ""),
            }
    except (httpx.HTTPError, ValueError) as exc:
        # 检查失败不缓存，下次请求重试
        return {"error": f"检查更新失败：{exc}"}
    with _release_lock:
        _release_cache = (time.monotonic(), result)
    return result


def _updater_alive() -> bool:
    if not WATCHTOWER_URL:
        return False
    try:
        return httpx.get(f"{WATCHTOWER_URL}/livez", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _update_status() -> dict[str, Any]:
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle", "time": 0}
    state = status.get("state", "idle")
    started = int(status.get("time") or 0)
    if state == "running":
        if status.get("from") != APP_VERSION:
            state = "success"
        elif time.time() - started > _RUNNING_TIMEOUT:
            state = "failed"
    return {"state": state, "time": started}


@router.get("/system/version")
def read_version(
    refresh: bool = Query(default=False), _user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    release = _latest_release(refresh)
    latest = release.get("version") or ""
    current_parsed, latest_parsed = _parse_version(APP_VERSION), _parse_version(latest)
    return ok(
        {
            "current": APP_VERSION,
            "latest": latest,
            "hasUpdate": bool(current_parsed and latest_parsed and latest_parsed > current_parsed),
            "releaseNotes": release.get("notes", ""),
            "releaseUrl": release.get("url", ""),
            "publishedAt": release.get("publishedAt", ""),
            "checkError": release.get("error", ""),
            "updateSupported": _updater_alive(),
            "updateStatus": _update_status(),
        }
    )


@router.post("/system/update")
def start_update(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if SUPER_ROLE not in user["roles"]:
        raise ApiError("只有超级管理员可以执行更新")
    if not _updater_alive():
        raise ApiError("未检测到更新服务 watchtower，请使用仓库中的 docker-compose.yml 部署")
    if _update_status()["state"] == "running":
        raise ApiError("正在更新中，请稍候")
    try:
        # async：watchtower 立即返回 202 并在后台更新，本进程随后会被它重启
        response = httpx.post(
            f"{WATCHTOWER_URL}/v1/update",
            params={"async": "true"},
            headers={"Authorization": f"Bearer {WATCHTOWER_TOKEN}"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise ApiError(f"调用更新服务失败：{exc}") from exc
    if response.status_code == 401:
        raise ApiError("更新服务令牌不一致，请确认 app 与 updater 使用同一个 UPDATE_TOKEN")
    if response.status_code == 429:
        raise ApiError("更新服务正忙，请稍后再试")
    if response.status_code >= 300:
        raise ApiError(f"更新服务返回错误（HTTP {response.status_code}）：{response.text[:200]}")
    STATUS_FILE.write_text(
        json.dumps({"state": "running", "time": int(time.time()), "from": APP_VERSION}), encoding="utf-8"
    )
    return ok({"current": APP_VERSION})
