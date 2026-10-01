"""系统管理：登录令牌、用户、角色、菜单与权限。

菜单表 sys_menus 是前端动态路由的唯一来源；角色通过 sys_role_menus 授权菜单，
用户登录后由 /api/route/getUserRoutes 返回其可访问的路由。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from . import db
from . import login_security as security


router = APIRouter(prefix="/api")

ACCESS_TOKEN_TTL = timedelta(hours=2)
SUPER_ROLE = "R_SUPER"
DEFAULT_HOME = "home"
DEFAULT_PASSWORD = "admin"
USER_NAME_PATTERN = re.compile(r"^[一-龥a-zA-Z0-9_-]{4,16}$")
PASSWORD_PATTERN = re.compile(r"^\w{6,18}$")
ROLE_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,39}$")
ROUTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*$")
# 删除或禁用这些菜单后将无人能再管理菜单
PROTECTED_MENUS = {"manage", "manage_menu"}
# 前端 views 中除业务页面外可被菜单引用的通用页面
BUILTIN_PAGES = ["403", "404", "500", "iframe-page"]

# 需与前端 .env 中的 VITE_SERVICE_LOGOUT_CODES / VITE_SERVICE_EXPIRED_TOKEN_CODES 保持一致
LOGOUT_CODE = "8888"
EXPIRED_TOKEN_CODE = "9999"


class ApiError(Exception):
    """以 {code, msg} 形式返回给前端的业务错误。"""

    def __init__(self, message: str, code: str = "1001") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def ok(data: Any = None) -> dict[str, Any]:
    return {"code": "0000", "data": data, "msg": ""}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _placeholders(values: Any) -> str:
    return ",".join("?" for _ in values)


# ---------------------------------------------------------------- 密码与令牌


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 200_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algorithm, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, digest)


def issue_token(user_id: int) -> dict[str, str]:
    now = _now()
    token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    refresh_ttl = timedelta(days=int(security.get_settings()["loginKeepDays"]))
    with db._db_lock, db.get_connection() as connection:
        connection.execute("DELETE FROM sys_tokens WHERE refresh_expires_at <= ?", (_iso(now),))
        connection.execute(
            """
            INSERT INTO sys_tokens (token, refresh_token, user_id, expires_at, refresh_expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token, refresh_token, user_id, _iso(now + ACCESS_TOKEN_TTL), _iso(now + refresh_ttl), _iso(now)),
        )
    return {"token": token, "refreshToken": refresh_token}


# ---------------------------------------------------------------- 建表与初始数据


def _page(route_name: str, menu_name: str, order: int, **extra: Any) -> dict[str, Any]:
    return {"route_name": route_name, "menu_name": menu_name, "order": order, **extra}


# 与 src/router/elegant/routes.ts 中实际使用的业务路由保持一致
MENU_SEED: list[dict[str, Any]] = [
    _page("home", "控制台", 0, component="layout.base$view.home", icon="mdi:view-dashboard-outline"),
    {
        "route_name": "domain-management",
        "icon": "mdi:web",
        "menu_name": "域名管理",
        "menu_type": "1",
        "component": "layout.base",
        "order": 1,
        "children": [
            _page("domain-management_expired", "过期域名列表", 1, icon="mdi:calendar-clock"),
            _page("domain-management_kicked", "踢出域名列表", 2, icon="mdi:web-remove"),
            _page("domain-management_qualified", "符合域名列表", 3, icon="mdi:check-decagram-outline"),
            _page("domain-management_unqualified", "不符合域名列表", 4, icon="mdi:close-circle-outline"),
            _page("domain-management_registered", "注册成功列表", 5, icon="mdi:trophy-outline"),
        ],
    },
    {
        "route_name": "runtime-control",
        "icon": "mdi:cog-play-outline",
        "menu_name": "运行控制",
        "menu_type": "1",
        "component": "layout.base",
        "order": 2,
        "children": [
            _page("runtime-control_expired-collection", "过期数据采集", 1, icon="mdi:database-import-outline"),
            _page("runtime-control_monitor-tasks", "监控任务", 2, icon="mdi:monitor-eye"),
            _page("runtime-control_query-tasks", "查询任务", 3, icon="mdi:text-search"),
        ],
    },
    {
        "route_name": "domain-generation",
        "icon": "mdi:auto-fix",
        "menu_name": "生成域名",
        "menu_type": "1",
        "component": "layout.base",
        "order": 3,
        "children": [
            _page("domain-generation_domain-generator", "域名生成", 1, icon="mdi:domain-plus"),
            _page("domain-generation_domain-dictionary", "字典管理", 2, icon="mdi:book-alphabet"),
        ],
    },
    {
        "route_name": "manage",
        "menu_name": "系统管理",
        "menu_type": "1",
        "component": "layout.base",
        "icon": "carbon:cloud-service-management",
        "order": 9,
        "children": [
            _page("manage_user", "用户管理", 1, icon="ic:round-manage-accounts"),
            _page("manage_role", "角色管理", 2, icon="carbon:user-role"),
            _page("manage_menu", "菜单管理", 3, icon="material-symbols:route", keep_alive=1),
            _page("manage_setting", "系统设置", 5, icon="mdi:cog-outline"),
            _page(
                "manage_user-detail",
                "用户详情",
                4,
                route_path="/manage/user-detail/:id",
                hide_in_menu=1,
                active_menu="manage_user",
            ),
        ],
    },
    _page("user-center", "个人中心", 99, component="layout.base$view.user-center", hide_in_menu=1),
]

ROLE_SEED = [
    ("R_SUPER", "超级管理员", "拥有全部菜单和按钮权限"),
    ("R_ADMIN", "管理员", "可使用域名管理、运行控制，并管理用户和菜单"),
    ("R_USER", "普通用户", "可使用域名管理和运行控制"),
]

# 全新数据库只创建一个超级管理员 admin/admin，首次登录后应立即修改密码
USER_SEED = [
    ("admin", "超级管理员", "R_SUPER"),
]

# 初始角色可访问的菜单，授权目录时连同其子菜单一起授权（R_SUPER 始终拥有全部菜单）
ROLE_MENU_SEED: dict[str, list[str]] = {
    "R_ADMIN": ["home", "domain-management", "runtime-control", "domain-generation", "manage_user", "manage_menu", "manage_user-detail", "user-center"],
    "R_USER": ["home", "domain-management", "runtime-control", "domain-generation", "user-center"],
}


def _walk_seed(items: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in MENU_SEED if items is None else items:
        result.append(item)
        result.extend(_walk_seed(item.get("children", [])))
    return result


def _seed_page_names() -> list[str]:
    names: list[str] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            if item.get("menu_type", "2") == "2":
                names.append(item["route_name"])
            walk(item.get("children", []))

    walk(MENU_SEED)
    return names


def _seed_menus(connection: Any, items: list[dict[str, Any]], parent_id: int, now: str) -> None:
    for item in items:
        route_name = item["route_name"]
        default_component = f"view.{route_name}" if parent_id else f"layout.base$view.{route_name}"
        cursor = connection.execute(
            """
            INSERT INTO sys_menus (
                parent_id, menu_type, menu_name, route_name, route_path, component, icon,
                i18n_key, order_num, keep_alive, hide_in_menu, active_menu,
                create_by, create_time, update_by, update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'system', ?, 'system', ?)
            """,
            (
                parent_id,
                item.get("menu_type", "2"),
                item["menu_name"],
                route_name,
                item.get("route_path") or "/" + route_name.replace("_", "/"),
                item.get("component", default_component),
                item.get("icon", ""),
                f"route.{route_name}",
                item["order"],
                item.get("keep_alive", 0),
                item.get("hide_in_menu", 0),
                item.get("active_menu", ""),
                now,
                now,
            ),
        )
        _seed_menus(connection, item.get("children", []), int(cursor.lastrowid), now)


def _migrate_home_menu(connection: Any, now: str) -> None:
    """已有库补充控制台菜单：授权给所有角色，并把仍使用旧默认首页的角色切换到控制台。"""
    if connection.execute("SELECT 1 FROM sys_menus WHERE route_name = 'home'").fetchone():
        return
    _seed_menus(connection, [MENU_SEED[0]], 0, now)
    menu_id = connection.execute("SELECT id FROM sys_menus WHERE route_name = 'home'").fetchone()[0]
    connection.execute(
        "INSERT OR IGNORE INTO sys_role_menus (role_id, menu_id) SELECT id, ? FROM sys_roles", (menu_id,)
    )
    connection.execute(
        "UPDATE sys_roles SET home = ? WHERE home IN ('', 'domain-management_expired')", (DEFAULT_HOME,)
    )


def _migrate_domain_generation_menu(connection: Any, now: str) -> None:
    """补齐动态路由模式下新增的域名生成菜单，并授权给现有角色。"""
    parent = connection.execute("SELECT id FROM sys_menus WHERE route_name = ?", ("domain-generation",)).fetchone()
    if parent is None:
        _seed_menus(connection, [item for item in MENU_SEED if item["route_name"] == "domain-generation"], 0, now)
        parent = connection.execute("SELECT id FROM sys_menus WHERE route_name = ?", ("domain-generation",)).fetchone()
    if parent is None:
        return
    parent_id = int(parent[0])
    children = [
        item for item in MENU_SEED
        if item["route_name"] == "domain-generation"
    ][0].get("children", [])
    for child in children:
        if not connection.execute("SELECT 1 FROM sys_menus WHERE route_name = ?", (child["route_name"],)).fetchone():
            _seed_menus(connection, [child], parent_id, now)
    menu_ids = [
        row["id"] for row in connection.execute(
            "SELECT id FROM sys_menus WHERE route_name IN (?, ?, ?)",
            ("domain-generation", "domain-generation_domain-generator", "domain-generation_domain-dictionary"),
        ).fetchall()
    ]
    roles = [row["id"] for row in connection.execute("SELECT id FROM sys_roles").fetchall()]
    connection.executemany(
        "INSERT OR IGNORE INTO sys_role_menus (role_id, menu_id) VALUES (?, ?)",
        [(role_id, menu_id) for role_id in roles for menu_id in menu_ids],
    )


def _migrate_registered_menu(connection: Any, now: str) -> None:
    """已有库补充注册成功列表菜单，授权给已拥有域名管理目录的角色。"""
    if connection.execute("SELECT 1 FROM sys_menus WHERE route_name = 'domain-management_registered'").fetchone():
        return
    parent = connection.execute("SELECT id FROM sys_menus WHERE route_name = 'domain-management'").fetchone()
    if parent is None:
        return
    group = next(item for item in MENU_SEED if item["route_name"] == "domain-management")
    item = next(child for child in group["children"] if child["route_name"] == "domain-management_registered")
    _seed_menus(connection, [item], int(parent[0]), now)
    menu_id = connection.execute("SELECT id FROM sys_menus WHERE route_name = 'domain-management_registered'").fetchone()[0]
    connection.execute(
        "INSERT OR IGNORE INTO sys_role_menus (role_id, menu_id) SELECT role_id, ? FROM sys_role_menus WHERE menu_id = ?",
        (menu_id, int(parent[0])),
    )


def _migrate_setting_menu(connection: Any, now: str) -> None:
    """已有库补充系统设置菜单；默认只有超级管理员可见，其他角色需在角色管理中授权。"""
    if connection.execute("SELECT 1 FROM sys_menus WHERE route_name = 'manage_setting'").fetchone():
        return
    parent = connection.execute("SELECT id FROM sys_menus WHERE route_name = 'manage'").fetchone()
    if parent is None:
        return
    manage = next(item for item in MENU_SEED if item["route_name"] == "manage")
    item = next(child for child in manage["children"] if child["route_name"] == "manage_setting")
    _seed_menus(connection, [item], int(parent[0]), now)


def init_system() -> None:
    with db._db_lock, db.get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sys_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                nick_name TEXT NOT NULL DEFAULT '',
                user_gender TEXT NOT NULL DEFAULT '',
                user_phone TEXT NOT NULL DEFAULT '',
                user_email TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '1',
                create_by TEXT NOT NULL DEFAULT '',
                create_time TEXT NOT NULL,
                update_by TEXT NOT NULL DEFAULT '',
                update_time TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sys_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_name TEXT NOT NULL,
                role_code TEXT NOT NULL UNIQUE,
                role_desc TEXT NOT NULL DEFAULT '',
                home TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '1',
                create_by TEXT NOT NULL DEFAULT '',
                create_time TEXT NOT NULL,
                update_by TEXT NOT NULL DEFAULT '',
                update_time TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sys_menus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id INTEGER NOT NULL DEFAULT 0,
                menu_type TEXT NOT NULL DEFAULT '2',
                menu_name TEXT NOT NULL,
                route_name TEXT NOT NULL UNIQUE,
                route_path TEXT NOT NULL,
                component TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT '',
                icon_type TEXT NOT NULL DEFAULT '1',
                i18n_key TEXT NOT NULL DEFAULT '',
                order_num INTEGER NOT NULL DEFAULT 0,
                keep_alive INTEGER NOT NULL DEFAULT 0,
                constant INTEGER NOT NULL DEFAULT 0,
                href TEXT NOT NULL DEFAULT '',
                hide_in_menu INTEGER NOT NULL DEFAULT 0,
                active_menu TEXT NOT NULL DEFAULT '',
                multi_tab INTEGER NOT NULL DEFAULT 0,
                fixed_index_in_tab INTEGER,
                query_json TEXT NOT NULL DEFAULT '[]',
                buttons_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT '1',
                create_by TEXT NOT NULL DEFAULT '',
                create_time TEXT NOT NULL,
                update_by TEXT NOT NULL DEFAULT '',
                update_time TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sys_user_roles (
                user_id INTEGER NOT NULL REFERENCES sys_users(id) ON DELETE CASCADE,
                role_id INTEGER NOT NULL REFERENCES sys_roles(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, role_id)
            );

            CREATE TABLE IF NOT EXISTS sys_role_menus (
                role_id INTEGER NOT NULL REFERENCES sys_roles(id) ON DELETE CASCADE,
                menu_id INTEGER NOT NULL REFERENCES sys_menus(id) ON DELETE CASCADE,
                PRIMARY KEY (role_id, menu_id)
            );

            CREATE TABLE IF NOT EXISTS sys_role_buttons (
                role_id INTEGER NOT NULL REFERENCES sys_roles(id) ON DELETE CASCADE,
                button_code TEXT NOT NULL,
                PRIMARY KEY (role_id, button_code)
            );

            CREATE TABLE IF NOT EXISTS sys_tokens (
                token TEXT PRIMARY KEY,
                refresh_token TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES sys_users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                refresh_expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sys_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sys_users)").fetchall()}
        for name in ("failed_attempts", "locked_until"):
            if name not in user_columns:
                definition = "INTEGER NOT NULL DEFAULT 0" if name == "failed_attempts" else "TEXT NOT NULL DEFAULT ''"
                connection.execute(f"ALTER TABLE sys_users ADD COLUMN {name} {definition}")
        now = db.utc_now()

        if not connection.execute("SELECT 1 FROM sys_menus LIMIT 1").fetchone():
            _seed_menus(connection, MENU_SEED, 0, now)
        else:
            # 为已有库中尚未设置图标的内置菜单补上默认图标，不覆盖用户自定义的图标
            connection.executemany(
                "UPDATE sys_menus SET icon = ? WHERE route_name = ? AND icon = ''",
                [(item["icon"], item["route_name"]) for item in _walk_seed() if item.get("icon")],
            )
            _migrate_home_menu(connection, now)

        if not connection.execute("SELECT 1 FROM sys_roles LIMIT 1").fetchone():
            menus = [dict(row) for row in connection.execute("SELECT id, parent_id, route_name FROM sys_menus")]
            menu_ids = {menu["route_name"]: menu["id"] for menu in menus}
            for code, name, desc in ROLE_SEED:
                cursor = connection.execute(
                    """
                    INSERT INTO sys_roles (role_name, role_code, role_desc, home, create_by, create_time, update_by, update_time)
                    VALUES (?, ?, ?, ?, 'system', ?, 'system', ?)
                    """,
                    (name, code, desc, DEFAULT_HOME, now, now),
                )
                route_names = list(menu_ids) if code == SUPER_ROLE else ROLE_MENU_SEED.get(code, [])
                granted = {menu_ids[route_name] for route_name in route_names if route_name in menu_ids}
                granted |= {menu["id"] for menu in menus if menu["parent_id"] in granted}
                connection.executemany(
                    "INSERT OR IGNORE INTO sys_role_menus (role_id, menu_id) VALUES (?, ?)",
                    [(int(cursor.lastrowid), menu_id) for menu_id in granted],
                )

        _migrate_domain_generation_menu(connection, now)
        _migrate_setting_menu(connection, now)
        _migrate_registered_menu(connection, now)

        if not connection.execute("SELECT 1 FROM sys_users LIMIT 1").fetchone():
            role_ids = {row["role_code"]: row["id"] for row in connection.execute("SELECT id, role_code FROM sys_roles")}
            for user_name, nick_name, role_code in USER_SEED:
                cursor = connection.execute(
                    """
                    INSERT INTO sys_users (user_name, password_hash, nick_name, create_by, create_time, update_by, update_time)
                    VALUES (?, ?, ?, 'system', ?, 'system', ?)
                    """,
                    (user_name, hash_password(DEFAULT_PASSWORD), nick_name, now, now),
                )
                if role_code in role_ids:
                    connection.execute(
                        "INSERT INTO sys_user_roles (user_id, role_id) VALUES (?, ?)",
                        (int(cursor.lastrowid), role_ids[role_code]),
                    )


# ---------------------------------------------------------------- 当前用户与权限


def _role_codes(user_id: int) -> list[str]:
    rows = db.fetch_all(
        """
        SELECT r.role_code FROM sys_user_roles ur
        JOIN sys_roles r ON r.id = ur.role_id
        WHERE ur.user_id = ? AND r.status = '1'
        ORDER BY r.id
        """,
        (user_id,),
    )
    return [row["role_code"] for row in rows]


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    token = _text(authorization).removeprefix("Bearer ").strip()
    if not token:
        raise ApiError("请先登录", LOGOUT_CODE)
    user = db.fetch_one(
        """
        SELECT u.*, t.expires_at AS token_expires_at FROM sys_tokens t
        JOIN sys_users u ON u.id = t.user_id
        WHERE t.token = ?
        """,
        (token,),
    )
    if not user:
        raise ApiError("登录状态已失效，请重新登录", LOGOUT_CODE)
    if user["status"] != "1":
        raise ApiError("账号已被禁用", LOGOUT_CODE)
    if user["token_expires_at"] <= _iso(_now()):
        raise ApiError("登录已过期", EXPIRED_TOKEN_CODE)
    user["roles"] = _role_codes(user["id"])
    return user


def _is_super(user: dict[str, Any]) -> bool:
    return SUPER_ROLE in user["roles"]


def authorized_menus(user: dict[str, Any]) -> list[dict[str, Any]]:
    """用户可访问的已启用菜单；授权了子菜单时自动包含其上级目录。"""
    menus = db.fetch_all("SELECT * FROM sys_menus WHERE status = '1' ORDER BY order_num, id")
    if _is_super(user):
        return menus
    ids = {
        row["menu_id"]
        for row in db.fetch_all(
            """
            SELECT rm.menu_id FROM sys_role_menus rm
            JOIN sys_user_roles ur ON ur.role_id = rm.role_id
            JOIN sys_roles r ON r.id = rm.role_id
            WHERE ur.user_id = ? AND r.status = '1'
            """,
            (user["id"],),
        )
    }
    parents = {menu["id"]: menu["parent_id"] for menu in menus}
    for menu_id in list(ids):
        parent_id = parents.get(menu_id)
        while parent_id and parent_id not in ids:
            ids.add(parent_id)
            parent_id = parents.get(parent_id)
    return [menu for menu in menus if menu["id"] in ids]


def require_menu(user: dict[str, Any], *route_names: str) -> None:
    if _is_super(user):
        return
    allowed = {menu["route_name"] for menu in authorized_menus(user)}
    if not allowed.intersection(route_names):
        raise ApiError("没有权限执行此操作", "1003")


def _ensure_super_user_exists(connection: Any) -> None:
    row = connection.execute(
        """
        SELECT COUNT(*) AS total FROM sys_users u
        JOIN sys_user_roles ur ON ur.user_id = u.id
        JOIN sys_roles r ON r.id = ur.role_id
        WHERE u.status = '1' AND r.status = '1' AND r.role_code = ?
        """,
        (SUPER_ROLE,),
    ).fetchone()
    if not row["total"]:
        raise ApiError("至少需要保留一个启用状态的超级管理员")


# ---------------------------------------------------------------- 登录


class LoginPayload(BaseModel):
    userName: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)
    captchaId: str = Field(default="", max_length=64)
    captchaCode: str = Field(default="", max_length=16)


class RefreshPayload(BaseModel):
    refreshToken: str = Field(default="", max_length=200)


class RegisterPayload(BaseModel):
    userName: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)
    email: str = Field(default="", max_length=200)
    emailCode: str = Field(default="", max_length=16)
    captchaId: str = Field(default="", max_length=64)
    captchaCode: str = Field(default="", max_length=16)


class EmailCodePayload(BaseModel):
    purpose: Literal["login", "reset", "register"]
    email: str = Field(min_length=3, max_length=200)
    captchaId: str = Field(default="", max_length=64)
    captchaCode: str = Field(default="", max_length=16)


class EmailLoginPayload(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    code: str = Field(min_length=1, max_length=16)


class ResetPasswordPayload(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    code: str = Field(min_length=1, max_length=16)
    password: str = Field(min_length=1, max_length=64)


def _require_captcha(captcha_id: str, captcha_code: str) -> None:
    if security.get_settings()["captchaEnabled"] and not security.verify_captcha(captcha_id, captcha_code):
        raise ApiError("图形验证码错误或已过期")


def _find_user_by_email(email: str) -> dict[str, Any] | None:
    return db.fetch_one("SELECT * FROM sys_users WHERE lower(user_email) = lower(?) ORDER BY id LIMIT 1", (email.strip(),))


def _check_user_usable(user: dict[str, Any]) -> None:
    if user["status"] != "1":
        raise ApiError("账号已被禁用或正在等待管理员审核")
    if user["locked_until"] and user["locked_until"] > _iso(_now()):
        remaining = (datetime.fromisoformat(user["locked_until"]) - _now()).total_seconds()
        raise ApiError(f"密码错误次数过多，账号已锁定，请 {max(1, math.ceil(remaining / 60))} 分钟后再试")


@router.get("/auth/captcha")
def get_captcha() -> dict[str, Any]:
    return ok(security.create_captcha())


@router.post("/auth/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    _require_captcha(payload.captchaId, payload.captchaCode)
    user = db.fetch_one("SELECT * FROM sys_users WHERE user_name = ?", (payload.userName.strip(),))
    if user:
        _check_user_usable(user)
    if not user or not verify_password(payload.password, user["password_hash"]):
        settings = security.get_settings()
        max_attempts = int(settings["lockMaxAttempts"])
        if user and max_attempts:
            attempts = int(user["failed_attempts"]) + 1
            if attempts >= max_attempts:
                locked_until = _iso(_now() + timedelta(minutes=int(settings["lockMinutes"])))
                db.execute("UPDATE sys_users SET failed_attempts = 0, locked_until = ? WHERE id = ?", (locked_until, user["id"]))
                raise ApiError(f"密码错误次数过多，账号已锁定 {settings['lockMinutes']} 分钟")
            db.execute("UPDATE sys_users SET failed_attempts = ? WHERE id = ?", (attempts, user["id"]))
            raise ApiError(f"用户名或密码错误，再错 {max_attempts - attempts} 次将锁定账号")
        raise ApiError("用户名或密码错误")
    if user["failed_attempts"] or user["locked_until"]:
        db.execute("UPDATE sys_users SET failed_attempts = 0, locked_until = '' WHERE id = ?", (user["id"],))
    return ok(issue_token(user["id"]))


@router.post("/auth/register")
def register(payload: RegisterPayload) -> dict[str, Any]:
    settings = security.get_settings()
    if not settings["registerEnabled"]:
        raise ApiError("系统未开放注册")
    email = payload.email.strip()
    mail_ready = security.email_configured(settings)
    if mail_ready:
        # 配置了邮箱时，注册必须验证邮箱，保证后续可用邮箱登录和找回密码
        if not EMAIL_PATTERN.fullmatch(email):
            raise ApiError("请输入正确的邮箱")
        if not security.verify_email_code("register", email, payload.emailCode):
            raise ApiError("邮箱验证码错误或已过期")
    else:
        _require_captcha(payload.captchaId, payload.captchaCode)
        if email and not EMAIL_PATTERN.fullmatch(email):
            raise ApiError("邮箱格式不正确")
    user_name = payload.userName.strip()
    if not USER_NAME_PATTERN.fullmatch(user_name):
        raise ApiError("用户名为 4-16 位中文、字母、数字、下划线或短横线")
    if not PASSWORD_PATTERN.fullmatch(payload.password):
        raise ApiError("密码为 6-18 位字母、数字或下划线")
    need_approval = bool(settings["registerNeedApproval"])
    now = db.utc_now()
    with db._db_lock, db.get_connection() as connection:
        if connection.execute("SELECT 1 FROM sys_users WHERE user_name = ?", (user_name,)).fetchone():
            raise ApiError("用户名已存在")
        if email and connection.execute("SELECT 1 FROM sys_users WHERE lower(user_email) = lower(?)", (email,)).fetchone():
            raise ApiError("该邮箱已被注册")
        cursor = connection.execute(
            """
            INSERT INTO sys_users (user_name, password_hash, nick_name, user_email, status,
                                   create_by, create_time, update_by, update_time)
            VALUES (?, ?, ?, ?, ?, 'register', ?, 'register', ?)
            """,
            (user_name, hash_password(payload.password), user_name, email, "2" if need_approval else "1", now, now),
        )
        role = connection.execute(
            "SELECT id FROM sys_roles WHERE role_code = ? AND role_code != ?", (settings["registerDefaultRole"], SUPER_ROLE)
        ).fetchone()
        if role:
            connection.execute("INSERT INTO sys_user_roles (user_id, role_id) VALUES (?, ?)", (int(cursor.lastrowid), role["id"]))
    return ok({"needApproval": need_approval})


@router.post("/auth/emailCode")
def send_email_code(payload: EmailCodePayload) -> dict[str, Any]:
    settings = security.get_settings()
    enabled = {
        "login": settings["emailLoginEnabled"],
        "reset": settings["passwordResetEnabled"],
        "register": settings["registerEnabled"],
    }[payload.purpose]
    if not enabled or not security.email_configured(settings):
        raise ApiError("该功能未开启")
    # 发送邮件前始终要求图形验证码，防止接口被用来批量发信
    if not security.verify_captcha(payload.captchaId, payload.captchaCode):
        raise ApiError("图形验证码错误或已过期")
    email = payload.email.strip()
    if not EMAIL_PATTERN.fullmatch(email):
        raise ApiError("请输入正确的邮箱")
    exists = _find_user_by_email(email) is not None
    if payload.purpose == "register" and exists:
        raise ApiError("该邮箱已被注册")
    # 登录和找回密码时不提示邮箱是否存在，避免被用来探测账号
    if payload.purpose != "register" and not exists:
        return ok()
    code = security.issue_email_code(payload.purpose, email)
    if code is None:
        raise ApiError("发送太频繁，请稍后再试")
    action = {"login": "登录", "reset": "重置密码", "register": "注册账号"}[payload.purpose]
    body = f"你正在{action}，验证码为：{code}\n\n验证码 5 分钟内有效。如非本人操作，请忽略本邮件。"
    try:
        security.send_mail(settings, email, f"{settings['systemTitle']} {action}验证码", body)
    except Exception as exc:
        security.discard_email_code(payload.purpose, email)
        raise ApiError("邮件发送失败，请联系管理员检查邮箱配置") from exc
    return ok()


@router.post("/auth/emailLogin")
def email_login(payload: EmailLoginPayload) -> dict[str, Any]:
    settings = security.get_settings()
    if not settings["emailLoginEnabled"]:
        raise ApiError("未开启邮箱验证码登录")
    user = _find_user_by_email(payload.email)
    if not user or not security.verify_email_code("login", payload.email, payload.code):
        raise ApiError("邮箱或验证码错误")
    _check_user_usable(user)
    return ok(issue_token(user["id"]))


@router.post("/auth/resetPassword")
def reset_password(payload: ResetPasswordPayload) -> dict[str, Any]:
    if not security.get_settings()["passwordResetEnabled"]:
        raise ApiError("未开启找回密码")
    if not PASSWORD_PATTERN.fullmatch(payload.password):
        raise ApiError("密码为 6-18 位字母、数字或下划线")
    user = _find_user_by_email(payload.email)
    if not user or not security.verify_email_code("reset", payload.email, payload.code):
        raise ApiError("邮箱或验证码错误")
    with db._db_lock, db.get_connection() as connection:
        connection.execute(
            "UPDATE sys_users SET password_hash = ?, failed_attempts = 0, locked_until = '', update_time = ? WHERE id = ?",
            (hash_password(payload.password), db.utc_now(), user["id"]),
        )
        connection.execute("DELETE FROM sys_tokens WHERE user_id = ?", (user["id"],))
    return ok()


@router.post("/auth/refreshToken")
def refresh_token(payload: RefreshPayload) -> dict[str, Any]:
    row = db.fetch_one(
        """
        SELECT t.user_id, t.refresh_expires_at, u.status FROM sys_tokens t
        JOIN sys_users u ON u.id = t.user_id
        WHERE t.refresh_token = ?
        """,
        (payload.refreshToken,),
    )
    # 刷新接口不能返回过期码，否则前端会陷入刷新循环
    if not row or row["refresh_expires_at"] <= _iso(_now()) or row["status"] != "1":
        raise ApiError("登录已过期，请重新登录", LOGOUT_CODE)
    db.execute("DELETE FROM sys_tokens WHERE refresh_token = ?", (payload.refreshToken,))
    return ok(issue_token(row["user_id"]))


def _all_buttons() -> list[dict[str, Any]]:
    buttons: dict[str, dict[str, Any]] = {}
    for menu in db.fetch_all("SELECT menu_name, buttons_json FROM sys_menus ORDER BY order_num, id"):
        for button in _json_list(menu["buttons_json"]):
            code = _text(button.get("code")) if isinstance(button, dict) else ""
            if code and code not in buttons:
                buttons[code] = {"code": code, "desc": _text(button.get("desc")), "menuName": menu["menu_name"]}
    return list(buttons.values())


def _user_buttons(user: dict[str, Any]) -> list[str]:
    if _is_super(user):
        return [button["code"] for button in _all_buttons()]
    rows = db.fetch_all(
        """
        SELECT DISTINCT rb.button_code FROM sys_role_buttons rb
        JOIN sys_user_roles ur ON ur.role_id = rb.role_id
        JOIN sys_roles r ON r.id = rb.role_id
        WHERE ur.user_id = ? AND r.status = '1'
        ORDER BY rb.button_code
        """,
        (user["id"],),
    )
    return [row["button_code"] for row in rows]


@router.get("/auth/getUserInfo")
def get_user_info(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return ok(
        {
            "userId": str(user["id"]),
            "userName": user["user_name"],
            "roles": user["roles"],
            "buttons": _user_buttons(user),
        }
    )


# ---------------------------------------------------------------- 系统设置


class SettingsPayload(BaseModel):
    systemTitle: str = Field(min_length=1, max_length=30)
    logo: str = Field(default="", max_length=300_000)
    loginKeepDays: int = Field(ge=1, le=90)
    captchaEnabled: bool = False
    lockMaxAttempts: int = Field(ge=0, le=100)
    lockMinutes: int = Field(ge=1, le=1440)
    registerEnabled: bool = False
    registerNeedApproval: bool = True
    registerDefaultRole: str = Field(min_length=1, max_length=40)
    emailLoginEnabled: bool = False
    passwordResetEnabled: bool = False
    smtpHost: str = Field(default="", max_length=200)
    smtpPort: int = Field(default=465, ge=1, le=65535)
    smtpSsl: bool = True
    smtpUser: str = Field(default="", max_length=200)
    smtpPassword: str | None = Field(default=None, max_length=200)
    smtpFrom: str = Field(default="", max_length=200)


class TestMailPayload(BaseModel):
    to: str = Field(min_length=3, max_length=200)


LOGO_PATTERN = re.compile(r"^data:image/(png|jpeg|gif|webp|svg\+xml|x-icon|vnd\.microsoft\.icon);base64,[A-Za-z0-9+/=]+$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("/system/settings")
def read_settings() -> dict[str, Any]:
    # 登录页在登录前就要读取，只返回可公开的配置
    return ok(security.public_settings())


@router.get("/system/settings/admin")
def read_admin_settings(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_setting")
    return ok(security.admin_settings())


@router.put("/system/settings")
def update_settings(payload: SettingsPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_setting")
    values = payload.model_dump()
    values["systemTitle"] = payload.systemTitle.strip()
    if not values["systemTitle"]:
        raise ApiError("请输入系统名称")
    if payload.logo and not LOGO_PATTERN.fullmatch(payload.logo):
        raise ApiError("站点图标格式不正确")
    if not db.fetch_one("SELECT 1 FROM sys_roles WHERE role_code = ?", (payload.registerDefaultRole,)):
        raise ApiError("注册默认角色不存在")
    if payload.registerDefaultRole == SUPER_ROLE:
        raise ApiError("注册用户不能默认为超级管理员")
    for key in ("smtpHost", "smtpUser", "smtpFrom"):
        values[key] = values[key].strip()
    if values["smtpFrom"] and not EMAIL_PATTERN.fullmatch(values["smtpFrom"]):
        raise ApiError("发件人邮箱格式不正确")
    if payload.smtpPassword is None:
        values.pop("smtpPassword")
    mail_ready = security.email_configured({**security.get_settings(), **values})
    if (payload.emailLoginEnabled or payload.passwordResetEnabled) and not mail_ready:
        raise ApiError("开启邮箱验证码登录或找回密码前，请先填写 SMTP 服务器和发件人")
    security.save_settings(values)
    return ok(security.admin_settings())


@router.post("/system/settings/testMail")
def test_mail(payload: TestMailPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_setting")
    settings = security.get_settings()
    if not security.email_configured(settings):
        raise ApiError("请先保存 SMTP 配置")
    try:
        security.send_mail(settings, payload.to.strip(), f"{settings['systemTitle']} 测试邮件", "邮件发送配置正常。")
    except Exception as exc:
        raise ApiError(f"发送失败：{exc}") from exc
    return ok()


# ---------------------------------------------------------------- 动态路由


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _children_map(menus: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    children: dict[int, list[dict[str, Any]]] = {}
    for menu in sorted(menus, key=lambda item: (item["order_num"], item["id"])):
        children.setdefault(menu["parent_id"], []).append(menu)
    return children


def _menu_to_route(menu: dict[str, Any], children_map: dict[int, list[dict[str, Any]]]) -> dict[str, Any] | None:
    # 菜单标题以菜单管理中保存的名称为准，不下发 i18nKey，否则前端会优先显示国际化文案，改名不生效
    meta: dict[str, Any] = {"title": menu["menu_name"], "order": menu["order_num"]}
    if menu["icon"]:
        meta["localIcon" if menu["icon_type"] == "2" else "icon"] = menu["icon"]
    for key, column in (("keepAlive", "keep_alive"), ("hideInMenu", "hide_in_menu"), ("multiTab", "multi_tab")):
        if menu[column]:
            meta[key] = True
    if menu["active_menu"]:
        meta["activeMenu"] = menu["active_menu"]
    if menu["href"]:
        meta["href"] = menu["href"]
    if menu["fixed_index_in_tab"] is not None:
        meta["fixedIndexInTab"] = menu["fixed_index_in_tab"]
    query = _json_list(menu["query_json"])
    if query:
        meta["query"] = query

    route: dict[str, Any] = {"id": str(menu["id"]), "name": menu["route_name"], "path": menu["route_path"], "meta": meta}
    if menu["component"]:
        route["component"] = menu["component"]
    children = [
        child_route
        for child in children_map.get(menu["id"], [])
        if (child_route := _menu_to_route(child, children_map)) is not None
    ]
    if children:
        route["children"] = children
    elif menu["menu_type"] == "1":
        # 没有可访问子菜单的目录不展示
        return None
    return route


@router.get("/route/getConstantRoutes")
def get_constant_routes() -> dict[str, Any]:
    """动态模式下前端启动时获取的常量路由（登录页、异常页等）。"""
    blank_pages = [
        {"name": name, "path": f"/{name}", "component": f"layout.blank$view.{name}"} for name in ("403", "404", "500")
    ]
    routes = [
        {**page, "meta": {"title": page["name"], "i18nKey": f"route.{page['name']}", "constant": True, "hideInMenu": True}}
        for page in blank_pages
    ]
    routes += [
        {
            "name": "iframe-page",
            "path": "/iframe-page/:url",
            "component": "layout.base$view.iframe-page",
            "props": True,
            "meta": {
                "title": "iframe-page",
                "i18nKey": "route.iframe-page",
                "constant": True,
                "hideInMenu": True,
                "keepAlive": True,
            },
        },
        {
            "name": "login",
            "path": "/login/:module(pwd-login|code-login|register|reset-pwd|bind-wechat)?",
            "component": "layout.blank$view.login",
            "props": True,
            "meta": {"title": "login", "i18nKey": "route.login", "constant": True, "hideInMenu": True},
        },
    ]
    return ok(routes)


@router.get("/route/getUserRoutes")
def get_user_routes(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    menus = authorized_menus(user)
    children_map = _children_map(menus)
    routes = [route for menu in children_map.get(0, []) if (route := _menu_to_route(menu, children_map))]

    route_names: set[str] = set()

    def collect(items: list[dict[str, Any]]) -> None:
        for item in items:
            route_names.add(item["name"])
            collect(item.get("children", []))

    collect(routes)
    pages = [
        menu
        for menu in menus
        if menu["menu_type"] == "2" and "view." in menu["component"] and menu["route_name"] in route_names
    ]
    page_names = {menu["route_name"] for menu in pages}
    role_homes = db.fetch_all(
        """
        SELECT r.home FROM sys_user_roles ur JOIN sys_roles r ON r.id = ur.role_id
        WHERE ur.user_id = ? AND r.status = '1' AND r.home != '' ORDER BY r.id
        """,
        (user["id"],),
    )
    candidates = [row["home"] for row in role_homes] + [DEFAULT_HOME]
    candidates += [menu["route_name"] for menu in pages if not menu["hide_in_menu"] and "/:" not in menu["route_path"]]
    home = next((name for name in candidates if name in page_names), "403")
    return ok({"routes": routes, "home": home})


@router.get("/route/isRouteExist")
def is_route_exist(routeName: str = Query(default=""), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return ok(routeName in {menu["route_name"] for menu in db.fetch_all("SELECT route_name FROM sys_menus")})


# ---------------------------------------------------------------- 通用


class IdsPayload(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


def _record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "createBy": row["create_by"],
        "createTime": row["create_time"],
        "updateBy": row["update_by"],
        "updateTime": row["update_time"],
    }


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _page_query(
    table: str,
    like_filters: tuple[tuple[str, Any], ...],
    equal_filters: tuple[tuple[str, Any], ...],
    current: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    conditions: list[str] = []
    params: list[Any] = []
    for column, value in like_filters:
        if _text(value):
            conditions.append(f"{column} LIKE ? ESCAPE '\\'")
            params.append(_like(_text(value)))
    for column, value in equal_filters:
        if _text(value):
            conditions.append(f"{column} = ?")
            params.append(_text(value))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    total = db.fetch_one(f"SELECT COUNT(*) AS total FROM {table}{where}", tuple(params))["total"]
    rows = db.fetch_all(
        f"SELECT * FROM {table}{where} ORDER BY id LIMIT ? OFFSET ?",
        (*params, size, (current - 1) * size),
    )
    return rows, total


# ---------------------------------------------------------------- 用户


class UserPayload(BaseModel):
    userName: str = Field(min_length=1, max_length=16)
    password: str | None = Field(default=None, max_length=64)
    userGender: Literal["1", "2", ""] | None = None
    nickName: str | None = Field(default="", max_length=40)
    userPhone: str | None = Field(default="", max_length=20)
    userEmail: str | None = Field(default="", max_length=100)
    userRoles: list[str] = Field(default_factory=list)
    status: Literal["1", "2"] = "1"


def _user_roles_map(user_ids: list[int]) -> dict[int, list[str]]:
    if not user_ids:
        return {}
    rows = db.fetch_all(
        f"""
        SELECT ur.user_id, r.role_code FROM sys_user_roles ur
        JOIN sys_roles r ON r.id = ur.role_id
        WHERE ur.user_id IN ({_placeholders(user_ids)}) ORDER BY r.id
        """,
        tuple(user_ids),
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["user_id"], []).append(row["role_code"])
    return result


@router.get("/systemManage/getUserList")
def get_user_list(
    current: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=200),
    userName: str | None = None,
    userGender: str | None = None,
    nickName: str | None = None,
    userPhone: str | None = None,
    userEmail: str | None = None,
    status: str | None = None,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_menu(user, "manage_user")
    rows, total = _page_query(
        "sys_users",
        (("user_name", userName), ("nick_name", nickName), ("user_phone", userPhone), ("user_email", userEmail)),
        (("user_gender", userGender), ("status", status)),
        current,
        size,
    )
    roles = _user_roles_map([row["id"] for row in rows])
    records = [
        {
            **_record(row),
            "userName": row["user_name"],
            "userGender": row["user_gender"] or None,
            "nickName": row["nick_name"],
            "userPhone": row["user_phone"],
            "userEmail": row["user_email"],
            "userRoles": roles.get(row["id"], []),
        }
        for row in rows
    ]
    return ok({"records": records, "current": current, "size": size, "total": total})


def _validate_user(payload: UserPayload, is_add: bool) -> tuple[str, str | None]:
    user_name = payload.userName.strip()
    if not USER_NAME_PATTERN.fullmatch(user_name):
        raise ApiError("用户名为 4-16 位中文、字母、数字、下划线或短横线")
    password = payload.password or ""
    if is_add and not password:
        raise ApiError("请输入密码")
    if password and not PASSWORD_PATTERN.fullmatch(password):
        raise ApiError("密码为 6-18 位字母、数字或下划线")
    return user_name, password or None


def _save_user_roles(connection: Any, user_id: int, role_codes: list[str]) -> None:
    codes = list(dict.fromkeys(code for code in role_codes if code))
    found: dict[str, int] = {}
    if codes:
        rows = connection.execute(
            f"SELECT id, role_code FROM sys_roles WHERE role_code IN ({_placeholders(codes)})", tuple(codes)
        ).fetchall()
        found = {row["role_code"]: row["id"] for row in rows}
        missing = [code for code in codes if code not in found]
        if missing:
            raise ApiError(f"角色不存在：{', '.join(missing)}")
    connection.execute("DELETE FROM sys_user_roles WHERE user_id = ?", (user_id,))
    connection.executemany(
        "INSERT INTO sys_user_roles (user_id, role_id) VALUES (?, ?)", [(user_id, found[code]) for code in codes]
    )


def _user_values(payload: UserPayload) -> tuple[str, str, str, str, str]:
    return (
        _text(payload.nickName),
        payload.userGender or "",
        _text(payload.userPhone),
        _text(payload.userEmail),
        payload.status,
    )


@router.post("/systemManage/addUser")
def add_user(payload: UserPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_user")
    user_name, password = _validate_user(payload, True)
    now = db.utc_now()
    with db._db_lock, db.get_connection() as connection:
        if connection.execute("SELECT 1 FROM sys_users WHERE user_name = ?", (user_name,)).fetchone():
            raise ApiError("用户名已存在")
        cursor = connection.execute(
            """
            INSERT INTO sys_users (
                user_name, password_hash, nick_name, user_gender, user_phone, user_email, status,
                create_by, create_time, update_by, update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_name, hash_password(password or ""), *_user_values(payload), user["user_name"], now, user["user_name"], now),
        )
        _save_user_roles(connection, int(cursor.lastrowid), payload.userRoles)
    return ok()


@router.put("/systemManage/updateUser/{user_id}")
def update_user(user_id: int, payload: UserPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_user")
    user_name, password = _validate_user(payload, False)
    is_self = user_id == user["id"]
    if is_self and payload.status != "1":
        raise ApiError("不能禁用当前登录的账号")
    with db._db_lock, db.get_connection() as connection:
        if not connection.execute("SELECT 1 FROM sys_users WHERE id = ?", (user_id,)).fetchone():
            raise ApiError("用户不存在")
        if connection.execute("SELECT 1 FROM sys_users WHERE user_name = ? AND id != ?", (user_name, user_id)).fetchone():
            raise ApiError("用户名已存在")
        connection.execute(
            """
            UPDATE sys_users SET user_name = ?, nick_name = ?, user_gender = ?, user_phone = ?, user_email = ?,
                status = ?, update_by = ?, update_time = ?
            WHERE id = ?
            """,
            (user_name, *_user_values(payload), user["user_name"], db.utc_now(), user_id),
        )
        if password:
            connection.execute(
                "UPDATE sys_users SET password_hash = ?, failed_attempts = 0, locked_until = '' WHERE id = ?",
                (hash_password(password), user_id),
            )
        _save_user_roles(connection, user_id, payload.userRoles)
        _ensure_super_user_exists(connection)
        # 被禁用或被他人重置密码后需要重新登录
        if payload.status != "1" or (password and not is_self):
            connection.execute("DELETE FROM sys_tokens WHERE user_id = ?", (user_id,))
    return ok()


@router.post("/systemManage/deleteUsers")
def delete_users(payload: IdsPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_user")
    if user["id"] in payload.ids:
        raise ApiError("不能删除当前登录的账号")
    with db._db_lock, db.get_connection() as connection:
        connection.execute(f"DELETE FROM sys_users WHERE id IN ({_placeholders(payload.ids)})", tuple(payload.ids))
        _ensure_super_user_exists(connection)
    return ok()


# ---------------------------------------------------------------- 角色


class RolePayload(BaseModel):
    roleName: str = Field(min_length=1, max_length=40)
    roleCode: str = Field(min_length=1, max_length=40)
    roleDesc: str | None = Field(default="", max_length=200)
    status: Literal["1", "2"] = "1"


class RoleMenuAuthPayload(BaseModel):
    home: str | None = Field(default="", max_length=100)
    menuIds: list[int] = Field(default_factory=list)


class RoleButtonAuthPayload(BaseModel):
    buttonCodes: list[str] = Field(default_factory=list)


def _get_role(role_id: int) -> dict[str, Any]:
    role = db.fetch_one("SELECT * FROM sys_roles WHERE id = ?", (role_id,))
    if not role:
        raise ApiError("角色不存在")
    return role


def _validate_role(payload: RolePayload) -> str:
    code = payload.roleCode.strip()
    if not ROLE_CODE_PATTERN.fullmatch(code):
        raise ApiError("角色编码以字母开头，只能包含字母、数字和下划线")
    return code


@router.get("/systemManage/getRoleList")
def get_role_list(
    current: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=200),
    roleName: str | None = None,
    roleCode: str | None = None,
    status: str | None = None,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    require_menu(user, "manage_role")
    rows, total = _page_query(
        "sys_roles", (("role_name", roleName), ("role_code", roleCode)), (("status", status),), current, size
    )
    records = [
        {**_record(row), "roleName": row["role_name"], "roleCode": row["role_code"], "roleDesc": row["role_desc"]}
        for row in rows
    ]
    return ok({"records": records, "current": current, "size": size, "total": total})


@router.get("/systemManage/getAllRoles")
def get_all_roles(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_user", "manage_role", "manage_menu", "manage_setting")
    rows = db.fetch_all("SELECT id, role_name, role_code FROM sys_roles WHERE status = '1' ORDER BY id")
    return ok([{"id": row["id"], "roleName": row["role_name"], "roleCode": row["role_code"]} for row in rows])


@router.post("/systemManage/addRole")
def add_role(payload: RolePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role")
    code = _validate_role(payload)
    now = db.utc_now()
    with db._db_lock, db.get_connection() as connection:
        if connection.execute("SELECT 1 FROM sys_roles WHERE role_code = ?", (code,)).fetchone():
            raise ApiError("角色编码已存在")
        connection.execute(
            """
            INSERT INTO sys_roles (role_name, role_code, role_desc, home, status, create_by, create_time, update_by, update_time)
            VALUES (?, ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (payload.roleName.strip(), code, _text(payload.roleDesc), payload.status, user["user_name"], now, user["user_name"], now),
        )
    return ok()


@router.put("/systemManage/updateRole/{role_id}")
def update_role(role_id: int, payload: RolePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role")
    code = _validate_role(payload)
    role = _get_role(role_id)
    if role["role_code"] == SUPER_ROLE and (code != SUPER_ROLE or payload.status != "1"):
        raise ApiError("超级管理员角色的编码和状态不能修改")
    with db._db_lock, db.get_connection() as connection:
        if connection.execute("SELECT 1 FROM sys_roles WHERE role_code = ? AND id != ?", (code, role_id)).fetchone():
            raise ApiError("角色编码已存在")
        connection.execute(
            """
            UPDATE sys_roles SET role_name = ?, role_code = ?, role_desc = ?, status = ?, update_by = ?, update_time = ?
            WHERE id = ?
            """,
            (payload.roleName.strip(), code, _text(payload.roleDesc), payload.status, user["user_name"], db.utc_now(), role_id),
        )
    return ok()


@router.post("/systemManage/deleteRoles")
def delete_roles(payload: IdsPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role")
    placeholders = _placeholders(payload.ids)
    if db.fetch_one(f"SELECT 1 FROM sys_roles WHERE role_code = ? AND id IN ({placeholders})", (SUPER_ROLE, *payload.ids)):
        raise ApiError("超级管理员角色不能删除")
    db.execute(f"DELETE FROM sys_roles WHERE id IN ({placeholders})", tuple(payload.ids))
    return ok()


@router.get("/systemManage/getRoleMenuAuth/{role_id}")
def get_role_menu_auth(role_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role")
    role = _get_role(role_id)
    rows = db.fetch_all("SELECT menu_id FROM sys_role_menus WHERE role_id = ? ORDER BY menu_id", (role_id,))
    return ok({"home": role["home"], "menuIds": [row["menu_id"] for row in rows]})


@router.put("/systemManage/updateRoleMenuAuth/{role_id}")
def update_role_menu_auth(
    role_id: int, payload: RoleMenuAuthPayload, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    require_menu(user, "manage_role")
    _get_role(role_id)
    menus = {row["id"]: row for row in db.fetch_all("SELECT id, route_name FROM sys_menus")}
    menu_ids = {menu_id for menu_id in payload.menuIds if menu_id in menus}
    home = _text(payload.home)
    if home and home not in {menus[menu_id]["route_name"] for menu_id in menu_ids}:
        raise ApiError("首页必须是已勾选的菜单")
    with db._db_lock, db.get_connection() as connection:
        connection.execute("DELETE FROM sys_role_menus WHERE role_id = ?", (role_id,))
        connection.executemany(
            "INSERT INTO sys_role_menus (role_id, menu_id) VALUES (?, ?)", [(role_id, menu_id) for menu_id in menu_ids]
        )
        connection.execute(
            "UPDATE sys_roles SET home = ?, update_by = ?, update_time = ? WHERE id = ?",
            (home, user["user_name"], db.utc_now(), role_id),
        )
    return ok()


@router.get("/systemManage/getAllButtons")
def get_all_buttons(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role")
    return ok(_all_buttons())


@router.get("/systemManage/getRoleButtonAuth/{role_id}")
def get_role_button_auth(role_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role")
    _get_role(role_id)
    rows = db.fetch_all("SELECT button_code FROM sys_role_buttons WHERE role_id = ? ORDER BY button_code", (role_id,))
    return ok([row["button_code"] for row in rows])


@router.put("/systemManage/updateRoleButtonAuth/{role_id}")
def update_role_button_auth(
    role_id: int, payload: RoleButtonAuthPayload, user: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    require_menu(user, "manage_role")
    _get_role(role_id)
    codes = {code for code in (_text(item) for item in payload.buttonCodes) if code}
    with db._db_lock, db.get_connection() as connection:
        connection.execute("DELETE FROM sys_role_buttons WHERE role_id = ?", (role_id,))
        connection.executemany(
            "INSERT INTO sys_role_buttons (role_id, button_code) VALUES (?, ?)", [(role_id, code) for code in codes]
        )
    return ok()


# ---------------------------------------------------------------- 菜单


class MenuQueryItem(BaseModel):
    key: str = Field(default="", max_length=100)
    value: str = Field(default="", max_length=500)


class MenuButtonItem(BaseModel):
    code: str = Field(default="", max_length=100)
    desc: str = Field(default="", max_length=100)


class MenuPayload(BaseModel):
    parentId: int = Field(default=0, ge=0)
    menuType: Literal["1", "2"] = "2"
    menuName: str = Field(min_length=1, max_length=40)
    routeName: str = Field(min_length=1, max_length=100)
    routePath: str = Field(min_length=1, max_length=200)
    component: str | None = Field(default="", max_length=200)
    icon: str | None = Field(default="", max_length=100)
    iconType: Literal["1", "2"] = "1"
    i18nKey: str | None = Field(default="", max_length=100)
    order: int | None = 0
    keepAlive: bool | None = False
    constant: bool | None = False
    href: str | None = Field(default="", max_length=500)
    hideInMenu: bool | None = False
    activeMenu: str | None = Field(default="", max_length=100)
    multiTab: bool | None = False
    fixedIndexInTab: int | None = None
    query: list[MenuQueryItem] | None = None
    buttons: list[MenuButtonItem] | None = None
    status: Literal["1", "2"] = "1"


def _public_menu(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_record(row),
        "parentId": row["parent_id"],
        "menuType": row["menu_type"],
        "menuName": row["menu_name"],
        "routeName": row["route_name"],
        "routePath": row["route_path"],
        "component": row["component"],
        "icon": row["icon"],
        "iconType": row["icon_type"],
        "i18nKey": row["i18n_key"] or None,
        "order": row["order_num"],
        "keepAlive": bool(row["keep_alive"]),
        "constant": bool(row["constant"]),
        "href": row["href"] or None,
        "hideInMenu": bool(row["hide_in_menu"]),
        "activeMenu": row["active_menu"] or None,
        "multiTab": bool(row["multi_tab"]),
        "fixedIndexInTab": row["fixed_index_in_tab"],
        "query": _json_list(row["query_json"]),
        "buttons": _json_list(row["buttons_json"]),
    }


def _menu_tree(rows: list[dict[str, Any]], transform: Any) -> list[dict[str, Any]]:
    children_map = _children_map(rows)

    def build(parent_id: int) -> list[dict[str, Any]]:
        items = []
        for row in children_map.get(parent_id, []):
            item = transform(row)
            children = build(row["id"])
            if children:
                item["children"] = children
            items.append(item)
        return items

    return build(0)


@router.get("/systemManage/getMenuList/v2")
def get_menu_list(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_menu")
    records = _menu_tree(db.fetch_all("SELECT * FROM sys_menus"), _public_menu)
    return ok({"records": records, "current": 1, "size": max(len(records), 1), "total": len(records)})


@router.get("/systemManage/getMenuTree")
def get_menu_tree(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role", "manage_menu")
    tree = _menu_tree(
        db.fetch_all("SELECT * FROM sys_menus"),
        lambda row: {
            "id": row["id"],
            "label": row["menu_name"] + ("（已禁用）" if row["status"] != "1" else ""),
            "pId": row["parent_id"],
            "routeName": row["route_name"],
            "isPage": row["menu_type"] == "2" and "view." in row["component"] and "/:" not in row["route_path"],
        },
    )
    return ok(tree)


@router.get("/systemManage/getAllPages")
def get_all_pages(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_role", "manage_menu")
    pages = dict.fromkeys(_seed_page_names())
    for row in db.fetch_all("SELECT component FROM sys_menus WHERE component LIKE '%view.%'"):
        pages[row["component"].split("view.", 1)[1]] = None
    for page in BUILTIN_PAGES:
        pages.setdefault(page, None)
    return ok(list(pages))


def _validate_menu(payload: MenuPayload, menu_id: int | None) -> dict[str, Any]:
    route_name = payload.routeName.strip()
    if not ROUTE_NAME_PATTERN.fullmatch(route_name):
        raise ApiError("路由名称只能包含字母、数字、短横线，层级之间用下划线分隔")
    route_path = payload.routePath.strip()
    if not route_path.startswith("/"):
        raise ApiError("路由路径必须以 / 开头")
    component = _text(payload.component)
    if payload.parentId:
        if not db.fetch_one("SELECT 1 FROM sys_menus WHERE id = ?", (payload.parentId,)):
            raise ApiError("父级菜单不存在")
        if menu_id is not None:
            parents = {row["id"]: row["parent_id"] for row in db.fetch_all("SELECT id, parent_id FROM sys_menus")}
            cursor: int | None = payload.parentId
            while cursor:
                if cursor == menu_id:
                    raise ApiError("父级菜单不能是自身或其子菜单")
                cursor = parents.get(cursor)
        if payload.menuType == "2" and not component.startswith("view."):
            raise ApiError("请选择页面组件")
    elif payload.menuType == "1" and not component.startswith("layout."):
        raise ApiError("一级目录请选择布局")
    elif payload.menuType == "2" and not re.fullmatch(r"layout\.[\w-]+\$view\.[\w-]+", component):
        raise ApiError("一级菜单请同时选择布局和页面组件")

    return {
        "parent_id": payload.parentId,
        "menu_type": payload.menuType,
        "menu_name": payload.menuName.strip(),
        "route_name": route_name,
        "route_path": route_path,
        "component": component,
        "icon": _text(payload.icon),
        "icon_type": payload.iconType,
        "i18n_key": _text(payload.i18nKey),
        "order_num": payload.order or 0,
        "keep_alive": int(bool(payload.keepAlive)),
        "constant": int(bool(payload.constant)),
        "href": _text(payload.href),
        "hide_in_menu": int(bool(payload.hideInMenu)),
        "active_menu": _text(payload.activeMenu),
        "multi_tab": int(bool(payload.multiTab)),
        "fixed_index_in_tab": payload.fixedIndexInTab,
        "query_json": json.dumps([item.model_dump() for item in payload.query or [] if item.key.strip()], ensure_ascii=False),
        "buttons_json": json.dumps(
            [item.model_dump() for item in payload.buttons or [] if item.code.strip()], ensure_ascii=False
        ),
        "status": payload.status,
    }


@router.post("/systemManage/addMenu")
def add_menu(payload: MenuPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_menu")
    values = _validate_menu(payload, None)
    now = db.utc_now()
    values.update(create_by=user["user_name"], create_time=now, update_by=user["user_name"], update_time=now)
    with db._db_lock, db.get_connection() as connection:
        if connection.execute("SELECT 1 FROM sys_menus WHERE route_name = ?", (values["route_name"],)).fetchone():
            raise ApiError("路由名称已存在")
        cursor = connection.execute(
            f"INSERT INTO sys_menus ({', '.join(values)}) VALUES ({_placeholders(values)})", tuple(values.values())
        )
        # 超级管理员角色的授权列表保持包含全部菜单
        connection.execute(
            "INSERT OR IGNORE INTO sys_role_menus (role_id, menu_id) SELECT id, ? FROM sys_roles WHERE role_code = ?",
            (int(cursor.lastrowid), SUPER_ROLE),
        )
    return ok()


@router.put("/systemManage/updateMenu/{menu_id}")
def update_menu(menu_id: int, payload: MenuPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_menu")
    menu = db.fetch_one("SELECT * FROM sys_menus WHERE id = ?", (menu_id,))
    if not menu:
        raise ApiError("菜单不存在")
    values = _validate_menu(payload, menu_id)
    if menu["route_name"] in PROTECTED_MENUS and (values["route_name"] != menu["route_name"] or values["status"] != "1"):
        raise ApiError("系统管理、菜单管理的路由名称和状态不能修改，否则将无法再管理菜单")
    values.update(update_by=user["user_name"], update_time=db.utc_now())
    assignments = ", ".join(f"{column} = ?" for column in values)
    with db._db_lock, db.get_connection() as connection:
        if connection.execute(
            "SELECT 1 FROM sys_menus WHERE route_name = ? AND id != ?", (values["route_name"], menu_id)
        ).fetchone():
            raise ApiError("路由名称已存在")
        connection.execute(f"UPDATE sys_menus SET {assignments} WHERE id = ?", (*values.values(), menu_id))
        if values["route_name"] != menu["route_name"]:
            connection.execute("UPDATE sys_roles SET home = ? WHERE home = ?", (values["route_name"], menu["route_name"]))
            connection.execute(
                "UPDATE sys_menus SET active_menu = ? WHERE active_menu = ?", (values["route_name"], menu["route_name"])
            )
    return ok()


@router.post("/systemManage/deleteMenus")
def delete_menus(payload: IdsPayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_menu(user, "manage_menu")
    ids = sorted(set(payload.ids))
    placeholders = _placeholders(ids)
    rows = db.fetch_all(f"SELECT id, route_name FROM sys_menus WHERE id IN ({placeholders})", tuple(ids))
    if any(row["route_name"] in PROTECTED_MENUS for row in rows):
        raise ApiError("系统管理、菜单管理不能删除，否则将无法再管理菜单")
    child = db.fetch_one(
        f"SELECT menu_name FROM sys_menus WHERE parent_id IN ({placeholders}) AND id NOT IN ({placeholders}) LIMIT 1",
        (*ids, *ids),
    )
    if child:
        raise ApiError(f"请先删除子菜单：{child['menu_name']}")
    route_names = [row["route_name"] for row in rows]
    with db._db_lock, db.get_connection() as connection:
        connection.execute(f"DELETE FROM sys_menus WHERE id IN ({placeholders})", tuple(ids))
        if route_names:
            connection.execute(
                f"UPDATE sys_roles SET home = '' WHERE home IN ({_placeholders(route_names)})", tuple(route_names)
            )
    return ok()
