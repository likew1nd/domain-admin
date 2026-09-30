"""版本检查与在线更新。

当前版本来自镜像构建参数 APP_VERSION；最新版本读取 GitHub Releases。
在线更新不直接操作 Docker：后端只在数据目录写入更新请求文件，由 compose 中的
updater 容器（deploy/updater.sh）拉取新镜像并重建 app 容器，并回写状态和心跳文件。
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

DATA_DIR = db.DB_PATH.parent
REQUEST_FILE = DATA_DIR / ".update-request"
STATUS_FILE = DATA_DIR / ".update-status"
HEARTBEAT_FILE = DATA_DIR / ".updater-alive"
LOG_FILE = DATA_DIR / "update.log"

# updater 每 5 秒写一次心跳，超过该时长视为未运行（非 Docker 部署时永远不存在）
_HEARTBEAT_TIMEOUT = 30
# 超过该时长仍为 running 视为更新器中途退出，允许重新发起
_RUNNING_TIMEOUT = 600
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
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime < _HEARTBEAT_TIMEOUT
    except OSError:
        return False


def _update_status() -> dict[str, Any]:
    try:
        status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle", "time": 0}
    state = status.get("state", "idle")
    started = int(status.get("time") or 0)
    if state == "running" and time.time() - started > _RUNNING_TIMEOUT:
        state = "failed"
    result: dict[str, Any] = {"state": state, "time": started}
    if state == "failed":
        try:
            result["log"] = "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
        except OSError:
            result["log"] = ""
    return result


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
        raise ApiError("未检测到更新服务，请使用 Docker 一键部署方式，或在服务器上重新执行安装脚本")
    if _update_status()["state"] == "running":
        raise ApiError("正在更新中，请稍候")
    STATUS_FILE.write_text(json.dumps({"state": "running", "time": int(time.time())}), encoding="utf-8")
    REQUEST_FILE.write_text(str(int(time.time())), encoding="utf-8")
    return ok({"current": APP_VERSION})
