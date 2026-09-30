from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import boce_client, dashboard, db, system_manage, updater
from .collector import queue_date, queue_file, queue_latest, queue_west_suffixes, shutdown_executor
from .crypto import decrypt_cookie, encrypt_cookie
from .query_service import query_manager
from .monitor_service import monitor_manager
from .registrar_adapters import RegistrarError, create_adapter as create_registrar_adapter
from .scheduler import scheduler
from .sources.base import SourceError
from .sources.registry import create_adapter


DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_suffix_cache: tuple[float, list[dict[str, Any]]] | None = None
_SUFFIX_CACHE_TTL = 300.0


def parse_length_filter(value: str | None) -> list[int]:
    lengths: set[int] = set()
    for token in (value or '').replace('，', ',').split(','):
        token = token.strip()
        if not token:
            continue
        if '-' in token:
            start, end = token.split('-', 1)
            if start.isdigit() and end.isdigit():
                lengths.update(range(int(start), int(end) + 1))
        elif token.isdigit():
            lengths.add(int(token))
    return sorted(length for length in lengths if 1 <= length <= 63)


def split_filter_values(value: str | None) -> list[str]:
    return [item.strip().lower().lstrip('.') for item in (value or '').replace('，', ',').split(',') if item.strip()]


def parse_composition_filter(value: str | None) -> list[str]:
    """域名组成筛选：逗号分隔的字符类别（letter,digit,chinese,symbol），为空表示不限。"""
    composition = db.normalize_composition(value or "")
    if value and value != "all" and not composition:
        raise fail("域名组成参数无效")
    return composition


def ok(data: Any) -> dict[str, Any]:
    return {"code": "0000", "data": data, "msg": ""}


def fail(message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=message)


GENERATED_SOURCE_KEY = "generated"


# 列表统计卡片的维度：返回键名即 domain_checks 中的字段名
STAT_FIELDS = (
    "deletion_status",
    "wechat_status",
    "qq_status",
    "pollution_status",
    "blocked_status",
    "blacklist_status",
    "filing_nature",
)
# 与列表展示保持一致：未查询时风险标签视为“否”，删除状态与备案性质为空
STAT_DEFAULTS = {field: "" if field in ("deletion_status", "filing_nature") else "否" for field in STAT_FIELDS}
# 按列序号分组，避免别名与连接表中的同名字段混淆
STAT_GROUP_BY = "GROUP BY " + ", ".join(str(index) for index in range(1, len(STAT_FIELDS) + 1))


def build_list_stats(
    status_sql: str, source_sql: str, params: tuple[Any, ...], unchecked_sql: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """按删除状态、风险标签、备案性质、数据来源汇总当前筛选结果。

    status_sql 需选出 STAT_FIELDS 中的各列以及 count；unchecked_sql 统计没有
    domain_checks 记录的域名数量，这部分按 STAT_DEFAULTS 计入。
    """
    counters: dict[str, dict[str, int]] = {field: {} for field in STAT_FIELDS}

    def add(row: dict[str, Any], count: int) -> None:
        for field in STAT_FIELDS:
            label = row.get(field) or ""
            counters[field][label] = counters[field].get(label, 0) + count

    for row in db.fetch_all(status_sql, params):
        add(row, int(row["count"]))
    if unchecked_sql:
        unchecked = db.fetch_one(unchecked_sql, params)
        if unchecked and unchecked["count"]:
            add(STAT_DEFAULTS, int(unchecked["count"]))
    stats: dict[str, list[dict[str, Any]]] = {
        field: sorted(({"label": label, "count": count} for label, count in values.items()), key=lambda item: -item["count"])
        for field, values in counters.items()
    }
    stats["source"] = [
        {"label": row["label"] or "", "count": int(row["count"])} for row in db.fetch_all(source_sql, params)
    ]
    return stats


class SourcePayload(BaseModel):
    source_key: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    adapter: Literal["gname", "west_cn", "generic"] = "generic"
    base_url: str = Field(min_length=1, max_length=500)
    page_path: str = Field(default="", max_length=500)
    download_template: str = Field(min_length=1, max_length=500)
    parser: Literal["line", "csv"] = "line"
    cookie: str | None = Field(default=None, max_length=20_000)
    clear_cookie: bool = False
    enabled: bool = True

    @field_validator("source_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,39}", value):
            raise ValueError("数据源标识只能使用小写字母、数字、下划线和短横线")
        return value


class GeneratedDomainsPayload(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=200_000)

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip().lower().replace("https://", "").replace("http://", "").split("/", 1)[0]
            if re.fullmatch(r"[a-z0-9\u4e00-\u9fff][a-z0-9\u4e00-\u9fff-]*\.[a-z0-9\u4e00-\u9fff.-]+", item):
                normalized.append(item)
        if not normalized:
            raise ValueError("没有有效域名")
        return list(dict.fromkeys(normalized))


class CollectPayload(BaseModel):
    source_id: int = Field(gt=0)
    requested_date: str
    file_format: Literal["txt", "csv"] = "txt"

    @field_validator("requested_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        if not DATE_PATTERN.fullmatch(value):
            raise ValueError("日期格式应为 YYYY-MM-DD")
        return value


class WestSuffixCollectPayload(BaseModel):
    source_id: int = Field(gt=0)
    suffixes: list[str] = Field(min_length=1, max_length=100)


class SchedulePayload(BaseModel):
    source_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=80)
    run_time: str
    suffixes: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @field_validator("run_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        if not TIME_PATTERN.fullmatch(value):
            raise ValueError("执行时间格式应为 HH:MM")
        return value


class QueryExceptionPayload(BaseModel):
    enabled: bool = False
    lengths: list[int] = Field(default_factory=list, max_length=63)
    suffixes: list[str] = Field(default_factory=list, max_length=100)
    patterns: list[str] = Field(default_factory=list, max_length=20)
    contains: list[str] = Field(default_factory=list, max_length=50)


def _composition_before(value: Any) -> Any:
    # 兼容旧版保存的单选值（all / english / alphanumeric ...）
    return db.normalize_composition(value) if isinstance(value, str) else value


class QueryTaskPayload(BaseModel):
    lengths: list[int] = Field(default_factory=list)
    suffixes: list[str] = Field(default_factory=list)
    exclude_chars: list[str] = Field(default_factory=list, max_length=100)
    domain_composition: list[Literal["letter", "digit", "chinese", "symbol"]] = Field(default_factory=list)
    delete_type: Literal["all", "expired", "redemption", "pending_delete"] = "expired"
    expiration_start: str = ""
    expiration_end: str = ""
    registration_start: str = ""
    registration_end: str = ""
    exceptions: QueryExceptionPayload = Field(default_factory=QueryExceptionPayload)
    threads: int = Field(default=5, ge=1, le=50)
    whois_retries: int = Field(default=2, ge=1, le=99)
    icp_retries: int = Field(default=2, ge=1, le=99)
    qq_retries: int = Field(default=1, ge=1, le=99)
    wechat_retries: int = Field(default=1, ge=1, le=99)
    douyin_retries: int = Field(default=1, ge=1, le=99)
    blocked_retries: int = Field(default=2, ge=1, le=99)
    pollution_retries: int = Field(default=2, ge=1, le=99)
    blacklist_retries: int = Field(default=2, ge=1, le=99)
    intercept_checks: list[Literal["wechat", "qq", "pollution", "blocked", "blacklist"]] = Field(default_factory=list)
    continuous: bool = True
    proxy_mode: Literal["direct", "tunnel", "api"] = "direct"
    proxy_endpoint: str = Field(default="", max_length=500)
    proxy_max_requests: int = Field(default=50, ge=1, le=10000)
    proxy_stages: list[Literal["whois", "icp", "blocked", "wechat", "douyin", "qq", "pollution", "blacklist"]] = Field(
        default_factory=lambda: ["icp"]
    )

    @field_validator("expiration_start", "expiration_end", "registration_start", "registration_end")
    @classmethod
    def validate_optional_date(cls, value: str) -> str:
        if value and not DATE_PATTERN.fullmatch(value):
            raise ValueError("日期格式应为 YYYY-MM-DD")
        return value

    @field_validator("domain_composition", mode="before")
    @classmethod
    def validate_composition(cls, value: Any) -> Any:
        return _composition_before(value)


class InterceptKeyPayload(BaseModel):
    api_key: str = Field(default="", max_length=500)


class QueryPreviewPayload(BaseModel):
    lengths: list[int] = Field(default_factory=list)
    suffixes: list[str] = Field(default_factory=list)
    exclude_chars: list[str] = Field(default_factory=list, max_length=100)
    domain_composition: list[Literal["letter", "digit", "chinese", "symbol"]] = Field(default_factory=list)

    @field_validator("domain_composition", mode="before")
    @classmethod
    def validate_composition(cls, value: Any) -> Any:
        return _composition_before(value)


class RegistrarApiPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    adapter: Literal["http_json", "aliyun_intl", "dynadot", "gname", "godaddy"] = "http_json"
    endpoint: str = Field(min_length=1, max_length=1000)
    token: str | None = Field(default=None, max_length=20_000)
    headers_json: str = Field(default="{}", max_length=20_000)
    config_json: str = Field(default="{}", max_length=50_000)
    clear_token: bool = False
    enabled: bool = True

    @field_validator("headers_json")
    @classmethod
    def validate_headers(cls, value: str) -> str:
        try:
            headers = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("自定义请求头必须是有效 JSON") from exc
        if not isinstance(headers, dict):
            raise ValueError("自定义请求头必须是 JSON 对象")
        return json.dumps(headers, ensure_ascii=False)

    @field_validator("config_json")
    @classmethod
    def validate_config(cls, value: str) -> str:
        try:
            config = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("平台配置必须是有效 JSON") from exc
        if not isinstance(config, dict):
            raise ValueError("平台配置必须是 JSON 对象")
        return json.dumps(config, ensure_ascii=False)


class MonitorSettingsPayload(BaseModel):
    interval_seconds: int = Field(default=30, ge=5, le=3600)
    concurrency: int = Field(default=5, ge=1, le=50)
    whois_retries: int = Field(default=2, ge=1, le=10)
    auto_register: bool = False
    auto_start: bool = False
    availability_api_id: int = Field(default=0, ge=0)
    api_ids: list[int] = Field(default_factory=list)


def public_query_task(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    result["filters"] = __import__("json").loads(result.pop("filters_json") or "{}")
    result["proxy"] = __import__("json").loads(result.pop("proxy_json") or "{}")
    return result


def public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: source[key]
        for key in (
            "id",
            "source_key",
            "name",
            "adapter",
            "base_url",
            "page_path",
            "download_template",
            "parser",
            "enabled",
            "created_at",
            "updated_at",
        )
    } | {"has_cookie": bool(source["cookie_ciphertext"])}


def public_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    result = dict(schedule)
    try:
        result["suffixes"] = json.loads(result.pop("suffixes_json", "[]") or "[]")
    except json.JSONDecodeError:
        result["suffixes"] = []
    return result


def public_registrar_api(api: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": api["id"],
        "name": api["name"],
        "adapter": api["adapter"],
        "endpoint": api["endpoint"],
        "headers_json": api["headers_json"],
        "config_json": api.get("config_json", "{}"),
        "enabled": bool(api["enabled"]),
        "has_token": bool(api["token_ciphertext"]),
        "created_at": api["created_at"],
        "updated_at": api["updated_at"],
    }


def get_source(source_id: int) -> dict[str, Any]:
    source = db.fetch_one("SELECT * FROM sources WHERE id = ? AND deleted_at = ''", (source_id,))
    if source is None:
        raise fail("数据源不存在", 404)
    return source


def ensure_date(value: str) -> None:
    if not DATE_PATTERN.fullmatch(value):
        raise fail("日期格式应为 YYYY-MM-DD")
    if value > date.today().isoformat():
        raise fail("不能采集未来日期")


def ensure_date_format(value: str) -> None:
    if not DATE_PATTERN.fullmatch(value):
        raise fail("日期格式应为 YYYY-MM-DD")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    system_manage.init_system()
    db.seed_gname_source(encrypt_cookie)
    db.seed_west_source(encrypt_cookie)
    scheduler.start()
    query_manager.resume_all()
    monitor_manager.resume_if_configured()
    dashboard.warm_up()
    yield
    monitor_manager.shutdown()
    scheduler.stop()
    query_manager.shutdown()
    shutdown_executor()


app = FastAPI(title="Domain Collector API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:9527", "http://127.0.0.1:9527"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 登录前可访问的接口；其余 /api 接口统一要求登录
PUBLIC_API_PATHS = {"/api/health", "/api/system/settings", "/api/route/getConstantRoutes"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/api/")
        and request.method != "OPTIONS"
        and path not in PUBLIC_API_PATHS
        and not path.startswith("/api/auth/")
    ):
        try:
            await run_in_threadpool(system_manage.current_user, request.headers.get("authorization"))
        except system_manage.ApiError as exc:
            return JSONResponse({"code": exc.code, "msg": exc.message, "data": None})
    return await call_next(request)


app.include_router(system_manage.router)
app.include_router(dashboard.router)
app.include_router(updater.router)


@app.exception_handler(system_manage.ApiError)
async def handle_api_error(_request: Request, exc: system_manage.ApiError) -> JSONResponse:
    return JSONResponse({"code": exc.code, "msg": exc.message, "data": None})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return ok({"status": "ok", "time": datetime.now().isoformat(timespec="seconds")})


@app.get("/api/sources")
def list_sources() -> dict[str, Any]:
    # 域名生成的来源只用于标记导入的域名，由单独的页面管理，不参与采集
    sources = db.fetch_all(
        "SELECT * FROM sources WHERE deleted_at = '' AND source_key != ? ORDER BY id", (GENERATED_SOURCE_KEY,)
    )
    return ok([public_source(source) for source in sources])


@app.post("/api/sources")
def create_source(payload: SourcePayload) -> dict[str, Any]:
    now = db.utc_now()
    try:
        source_id = db.execute(
            """
            INSERT INTO sources (
                source_key, name, adapter, base_url, page_path, download_template,
                parser, cookie_ciphertext, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.source_key,
                payload.name,
                payload.adapter,
                payload.base_url.rstrip("/"),
                payload.page_path,
                payload.download_template,
                payload.parser,
                encrypt_cookie(payload.cookie or ""),
                int(payload.enabled),
                now,
                now,
            ),
        )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise fail("数据源标识已存在") from exc
        raise
    return ok(public_source(get_source(source_id)))


@app.put("/api/sources/{source_id}")
def update_source(source_id: int, payload: SourcePayload) -> dict[str, Any]:
    source = get_source(source_id)
    cookie_ciphertext = source["cookie_ciphertext"]
    if payload.clear_cookie:
        cookie_ciphertext = ""
    elif payload.cookie is not None and payload.cookie.strip():
        cookie_ciphertext = encrypt_cookie(payload.cookie)

    try:
        db.execute(
            """
            UPDATE sources
            SET source_key = ?, name = ?, adapter = ?, base_url = ?, page_path = ?,
                download_template = ?, parser = ?, cookie_ciphertext = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.source_key,
                payload.name,
                payload.adapter,
                payload.base_url.rstrip("/"),
                payload.page_path,
                payload.download_template,
                payload.parser,
                cookie_ciphertext,
                int(payload.enabled),
                db.utc_now(),
                source_id,
            ),
        )
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise fail("数据源标识已存在") from exc
        raise
    return ok(public_source(get_source(source_id)))


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int) -> dict[str, Any]:
    source = get_source(source_id)
    if source["source_key"] == GENERATED_SOURCE_KEY:
        raise fail("域名生成数据源由系统维护，不能删除")
    removed = db.soft_delete_source(source_id)
    return ok({"deleted": source_id, "removed_schedules": removed})


@app.post("/api/sources/{source_id}/test")
def test_source(source_id: int) -> dict[str, Any]:
    source = get_source(source_id)
    adapter = None
    try:
        adapter = create_adapter(source, decrypt_cookie(source["cookie_ciphertext"]))
        dates = adapter.available_dates()
        if dates and source.get("adapter") != "west_cn":
            adapter.download(dates[0], "txt")
        return ok({"authenticated": True, "available_dates": dates[:60]})
    except SourceError as exc:
        raise fail(str(exc), 422) from exc
    finally:
        if adapter is not None:
            adapter.close()


@app.get("/api/registrar-apis")
def list_registrar_apis() -> dict[str, Any]:
    rows = db.fetch_all("SELECT * FROM registrar_apis ORDER BY id")
    return ok([public_registrar_api(row) for row in rows])


@app.post("/api/registrar-apis")
def create_registrar_api(payload: RegistrarApiPayload) -> dict[str, Any]:
    timestamp = db.utc_now()
    api_id = db.execute(
        """
        INSERT INTO registrar_apis (name, adapter, endpoint, token_ciphertext, headers_json, config_json, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.adapter,
            payload.endpoint.strip(),
            encrypt_cookie(payload.token or ""),
            payload.headers_json,
            payload.config_json,
            int(payload.enabled),
            timestamp,
            timestamp,
        ),
    )
    row = db.fetch_one("SELECT * FROM registrar_apis WHERE id = ?", (api_id,))
    return ok(public_registrar_api(row or {}))


@app.put("/api/registrar-apis/{api_id}")
def update_registrar_api(api_id: int, payload: RegistrarApiPayload) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM registrar_apis WHERE id = ?", (api_id,))
    if row is None:
        raise fail("注册商 API 不存在", 404)
    token_ciphertext = row["token_ciphertext"]
    if payload.clear_token:
        token_ciphertext = ""
    elif payload.token is not None and payload.token.strip():
        token_ciphertext = encrypt_cookie(payload.token.strip())
    db.execute(
        """
        UPDATE registrar_apis
        SET name = ?, adapter = ?, endpoint = ?, token_ciphertext = ?, headers_json = ?, config_json = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name,
            payload.adapter,
            payload.endpoint.strip(),
            token_ciphertext,
            payload.headers_json,
            payload.config_json,
            int(payload.enabled),
            db.utc_now(),
            api_id,
        ),
    )
    updated = db.fetch_one("SELECT * FROM registrar_apis WHERE id = ?", (api_id,))
    return ok(public_registrar_api(updated or {}))


@app.delete("/api/registrar-apis/{api_id}")
def delete_registrar_api(api_id: int) -> dict[str, Any]:
    if db.fetch_one("SELECT id FROM registrar_apis WHERE id = ?", (api_id,)) is None:
        raise fail("注册商 API 不存在", 404)
    try:
        db.execute("DELETE FROM registrar_apis WHERE id = ?", (api_id,))
    except Exception as exc:
        if "FOREIGN KEY" in str(exc):
            raise fail("该 API 已有抢注记录，请改为停用后保留") from exc
        raise
    current = monitor_manager.settings()
    if api_id in current["api_ids"]:
        current["api_ids"] = [value for value in current["api_ids"] if value != api_id]
        monitor_manager.save_settings(current)
    return ok({"deleted": api_id})


@app.post("/api/registrar-apis/{api_id}/test")
def test_registrar_api(api_id: int) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM registrar_apis WHERE id = ?", (api_id,))
    if row is None:
        raise fail("注册商 API 不存在", 404)
    try:
        adapter = create_registrar_adapter(row, decrypt_cookie(row["token_ciphertext"]))
        success, status_code, response = adapter.test()
    except RegistrarError as exc:
        raise fail(str(exc), 422) from exc
    return ok({"success": success, "status_code": status_code, "response": response})


@app.get("/api/monitor/settings")
def get_monitor_settings() -> dict[str, Any]:
    return ok(monitor_manager.settings())


@app.put("/api/monitor/settings")
def save_monitor_settings(payload: MonitorSettingsPayload) -> dict[str, Any]:
    api_rows = db.fetch_all("SELECT id, enabled FROM registrar_apis")
    available_ids = {int(row["id"]) for row in api_rows}
    enabled_ids = {int(row["id"]) for row in api_rows if row["enabled"]}
    settings = payload.model_dump()
    settings["api_ids"] = [api_id for api_id in settings["api_ids"] if api_id in available_ids]
    settings["availability_api_id"] = (
        settings["availability_api_id"] if settings["availability_api_id"] in enabled_ids else 0
    )
    return ok(monitor_manager.save_settings(settings))


@app.get("/api/monitor/status")
def get_monitor_status() -> dict[str, Any]:
    return ok(monitor_manager.status())


@app.post("/api/monitor/start")
def start_monitor() -> dict[str, Any]:
    try:
        return ok(monitor_manager.start())
    except ValueError as exc:
        raise fail(str(exc), 422) from exc


@app.post("/api/monitor/stop")
def stop_monitor() -> dict[str, Any]:
    return ok(monitor_manager.stop())


@app.get("/api/monitor/logs")
def get_monitor_logs(limit: int = Query(default=500, ge=1, le=500)) -> dict[str, Any]:
    return ok(monitor_manager.logs(limit))


@app.get("/api/kicked-domains")
def list_kicked_domains(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    domain: str | None = None,
    domain_composition: str | None = None,
    length: str | None = None,
    suffix: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    deletion_status: str | None = None,
    registration_start: str | None = None,
    registration_end: str | None = None,
    expiration_start: str | None = None,
    expiration_end: str | None = None,
    wechat_status: str | None = None,
    qq_status: str | None = None,
    pollution_status: str | None = None,
    blocked_status: str | None = None,
    blacklist_status: str | None = None,
    filing_nature: str | None = None,
    filing_info: str | None = None,
    include_chars: str | None = None,
    exclude_chars: str | None = None,
    reason: str | None = None,
    kicked_start: str | None = None,
    kicked_end: str | None = None,
    with_stats: bool = False,
) -> dict[str, Any]:
    conditions = ["1 = 1"]
    params: list[Any] = []
    if domain:
        conditions.append("k.domain LIKE ?")
        params.append(f"%{domain.strip()}%")
    if composition := parse_composition_filter(domain_composition):
        label_expression = "substr(k.domain, 1, length(k.domain) - instr(reverse(k.domain), '.'))"
        condition, kinds = db.label_kind_condition(composition, f"label_kind_of({label_expression})")
        conditions.append(condition)
        params.extend(kinds)
    lengths = parse_length_filter(length)
    if lengths:
        placeholders = ','.join('?' for _ in lengths)
        label_expression = "substr(k.domain, 1, length(k.domain) - instr(reverse(k.domain), '.'))"
        conditions.append(f"length({label_expression}) IN ({placeholders})")
        params.extend(lengths)
    if suffix:
        suffixes = split_filter_values(suffix)
        if suffixes:
            conditions.append("(" + " OR ".join("lower(k.domain) LIKE ?" for _ in suffixes) + ")")
            params.extend(f"%.{item}" for item in suffixes)
    if keyword:
        value = f"%{keyword.strip()}%"
        keyword_fields = [
            "k.domain",
            "COALESCE(NULLIF(k.deletion_status, ''), c.deletion_status, '')",
            "COALESCE(NULLIF(k.creation_date, ''), c.creation_date, '')",
            "COALESCE(NULLIF(k.expiration_date, ''), c.expiration_date, '')",
            "COALESCE(NULLIF(k.wechat_status, ''), c.wechat_status, '否')",
            "COALESCE(NULLIF(k.qq_status, ''), c.qq_status, '否')",
            "COALESCE(NULLIF(k.pollution_status, ''), c.pollution_status, '否')",
            "COALESCE(NULLIF(k.blocked_status, ''), c.blocked_status, '否')",
            "COALESCE(NULLIF(k.blacklist_status, ''), c.blacklist_status, '否')",
            "COALESCE(NULLIF(k.filing_nature, ''), c.filing_nature, '')",
            "COALESCE(NULLIF(k.filing_info, ''), c.filing_info, '')",
            "d.source_name",
            "k.reason",
            "k.detail",
            "k.kicked_at"
        ]
        conditions.append(f"({ ' OR '.join(f'{field} LIKE ?' for field in keyword_fields) })")
        params.extend([value] * len(keyword_fields))
    if source:
        conditions.append("d.source_name LIKE ?")
        params.append(f"%{source.strip()}%")
    if reason:
        conditions.append("k.reason LIKE ?")
        params.append(f"%{reason.strip()}%")
    if include_chars:
        conditions.append("lower(k.domain) LIKE ?")
        params.append(f"%{include_chars.strip().lower()}%")
    if exclude_chars:
        conditions.append("lower(k.domain) NOT LIKE ?")
        params.append(f"%{exclude_chars.strip().lower()}%")
    for field, value in (("COALESCE(NULLIF(k.deletion_status, ''), c.deletion_status)", deletion_status),
                         ("COALESCE(NULLIF(k.wechat_status, ''), c.wechat_status, '否')", wechat_status),
                         ("COALESCE(NULLIF(k.qq_status, ''), c.qq_status, '否')", qq_status),
                         ("COALESCE(NULLIF(k.pollution_status, ''), c.pollution_status, '否')", pollution_status),
                         ("COALESCE(NULLIF(k.blocked_status, ''), c.blocked_status, '否')", blocked_status),
                         ("COALESCE(NULLIF(k.blacklist_status, ''), c.blacklist_status, '否')", blacklist_status),
                         ("COALESCE(NULLIF(k.filing_nature, ''), c.filing_nature)", filing_nature)):
        if value:
            conditions.append(f"{field} = ?")
            params.append(value)
    if filing_info:
        conditions.append("COALESCE(NULLIF(k.filing_info, ''), c.filing_info) LIKE ?")
        params.append(f"%{filing_info.strip()}%")
    for field, start, end in (("COALESCE(NULLIF(k.creation_date, ''), c.creation_date)", registration_start, registration_end),
                              ("COALESCE(NULLIF(k.expiration_date, ''), c.expiration_date)", expiration_start, expiration_end),
                              ("k.kicked_at", kicked_start, kicked_end)):
        if start:
            ensure_date_format(start)
            conditions.append(f"substr({field}, 1, 10) >= ?" if field == "k.kicked_at" else f"{field} >= ?")
            params.append(start)
        if end:
            ensure_date_format(end)
            conditions.append(f"substr({field}, 1, 10) <= ?" if field == "k.kicked_at" else f"{field} <= ?")
            params.append(end)
    where = " AND ".join(conditions)
    total = db.fetch_one(
        f"SELECT COUNT(*) AS total FROM kicked_domains k LEFT JOIN domains d ON d.domain = k.domain LEFT JOIN domain_checks c ON c.domain = k.domain WHERE {where}",
        tuple(params),
    )["total"]
    rows = db.fetch_all(
        f"""
        SELECT k.domain,
               COALESCE(NULLIF(k.deletion_status, ''), c.deletion_status, '') AS deletion_status,
               COALESCE(NULLIF(k.creation_date, ''), c.creation_date, '') AS creation_date,
               COALESCE(NULLIF(k.expiration_date, ''), c.expiration_date, '') AS expiration_date,
               COALESCE(NULLIF(k.wechat_status, ''), c.wechat_status, '否') AS wechat_status,
               COALESCE(NULLIF(k.qq_status, ''), c.qq_status, '否') AS qq_status,
               COALESCE(NULLIF(k.pollution_status, ''), c.pollution_status, '否') AS pollution_status,
               COALESCE(NULLIF(k.blocked_status, ''), c.blocked_status, '否') AS blocked_status,
               COALESCE(NULLIF(k.blacklist_status, ''), c.blacklist_status, '否') AS blacklist_status,
               COALESCE(NULLIF(k.filing_nature, ''), c.filing_nature, '') AS filing_nature,
               COALESCE(NULLIF(k.filing_info, ''), c.filing_info, '') AS filing_info,
               d.source_name AS source,
               k.reason, k.detail, k.kicked_at
        FROM kicked_domains k
        LEFT JOIN domains d ON d.domain = k.domain
        LEFT JOIN domain_checks c ON c.domain = k.domain
        WHERE {where}
        ORDER BY k.kicked_at DESC, k.domain
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, (page - 1) * page_size),
    )
    data: dict[str, Any] = {"records": rows, "current": page, "size": page_size, "total": total}
    if with_stats:
        joins = "FROM kicked_domains k LEFT JOIN domains d ON d.domain = k.domain LEFT JOIN domain_checks c ON c.domain = k.domain"
        select = ", ".join(
            f"COALESCE(NULLIF(k.{field}, ''), c.{field}, '{STAT_DEFAULTS[field]}') AS {field}" for field in STAT_FIELDS
        )
        data["stats"] = build_list_stats(
            f"SELECT {select}, COUNT(*) AS count {joins} WHERE {where} {STAT_GROUP_BY}",
            f"SELECT d.source_name AS label, COUNT(*) AS count {joins} WHERE {where} GROUP BY d.source_name ORDER BY count DESC",
            tuple(params),
        )
    return ok(data)


@app.delete("/api/kicked-domains")
def clear_kicked_domains() -> dict[str, Any]:
    row = db.fetch_one("SELECT COUNT(*) AS total FROM kicked_domains")
    db.execute("DELETE FROM kicked_domains")
    return ok({"deleted": int(row["total"] if row else 0)})


@app.post("/api/collect")
def collect(payload: CollectPayload) -> dict[str, Any]:
    ensure_date(payload.requested_date)
    try:
        run_id = queue_date(payload.source_id, payload.requested_date, payload.file_format, "manual")
    except SourceError as exc:
        raise fail(str(exc), 422) from exc
    return ok({"run_id": run_id})


@app.post("/api/collect/latest")
def collect_latest(source_id: int = Query(gt=0), file_format: Literal["txt", "csv"] = "txt") -> dict[str, Any]:
    try:
        run_id = queue_latest(source_id, file_format, "manual-latest")
    except SourceError as exc:
        raise fail(str(exc), 422) from exc
    return ok({"run_id": run_id})


@app.post("/api/collect/west")
def collect_west(payload: WestSuffixCollectPayload) -> dict[str, Any]:
    try:
        run_ids = queue_west_suffixes(payload.source_id, payload.suffixes, None, "manual-west")
    except SourceError as exc:
        raise fail(str(exc), 422) from exc
    return ok({"run_ids": run_ids})


@app.post("/api/collect/upload")
async def collect_upload(
    source_id: int = Query(gt=0),
    requested_date: str = Query(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    ensure_date(requested_date)
    filename = file.filename or "upload.txt"
    if not filename.lower().endswith(".txt"):
        raise fail("只支持上传 TXT 文件", 422)
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise fail("TXT 文件不能超过 50 MB", 422)
    try:
        run_id = queue_file(source_id, requested_date, content, filename)
    except SourceError as exc:
        raise fail(str(exc), 422) from exc
    return ok({"run_id": run_id})


@app.get("/api/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    scope: Literal["all", "manual", "scheduled"] = "all",
) -> dict[str, Any]:
    condition = "1 = 1"
    if scope == "manual":
        condition = "trigger_type IN ('manual', 'manual-latest', 'manual-file', 'manual-west') OR trigger_type LIKE 'manual-west:%'"
    elif scope == "scheduled":
        condition = "trigger_type IN ('schedule', 'manual-schedule')"
    return ok(db.fetch_all(f"SELECT * FROM import_runs WHERE {condition} ORDER BY id DESC LIMIT ?", (limit,)))


@app.get("/api/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
    if run is None:
        raise fail("采集任务不存在", 404)
    return ok(run)


@app.get("/api/domains")
def list_domains(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    length: str | None = None,
    suffix: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_chars: str | None = None,
    exclude_chars: str | None = None,
    domain: str | None = None,
    domain_composition: str | None = None,
    keyword: str | None = None,
    source: str | None = None,
    deletion_status: str | None = None,
    registration_start: str | None = None,
    registration_end: str | None = None,
    expiration_start: str | None = None,
    expiration_end: str | None = None,
    wechat_status: str | None = None,
    qq_status: str | None = None,
    pollution_status: str | None = None,
    blocked_status: str | None = None,
    blacklist_status: str | None = None,
    filing_nature: str | None = None,
    filing_info: str | None = None,
    query_start: str | None = None,
    query_end: str | None = None,
    with_stats: bool = False,
) -> dict[str, Any]:
    conditions = ["1 = 1"]
    params: list[Any] = []
    if domain:
        conditions.append("d.domain LIKE ?")
        params.append(f"%{domain.strip()}%")
    if composition := parse_composition_filter(domain_composition):
        condition, kinds = db.label_kind_condition(composition, "d.label_kind")
        conditions.append(condition)
        params.extend(kinds)
    if keyword:
        value = f"%{keyword.strip()}%"
        keyword_fields = [
            "d.domain",
            "COALESCE(c.deletion_status, '')",
            "COALESCE(c.creation_date, '')",
            "COALESCE(c.expiration_date, '')",
            "COALESCE(c.wechat_status, '否')",
            "COALESCE(c.qq_status, '否')",
            "COALESCE(c.pollution_status, '否')",
            "COALESCE(c.blocked_status, '否')",
            "COALESCE(c.blacklist_status, '否')",
            "COALESCE(c.filing_nature, '')",
            "COALESCE(c.filing_info, '')",
            "d.source_name",
            "d.joined_at",
            "COALESCE(NULLIF(d.query_time, ''), c.checked_at, '')"
        ]
        conditions.append(f"({ ' OR '.join(f'{field} LIKE ?' for field in keyword_fields) })")
        params.extend([value] * len(keyword_fields))
    if source:
        conditions.append("d.source_name LIKE ?")
        params.append(f"%{source.strip()}%")
    lengths = parse_length_filter(length)
    if lengths:
        placeholders = ','.join('?' for _ in lengths)
        conditions.append(f"length(substr(d.domain, 1, length(d.domain) - instr(reverse(d.domain), '.') )) IN ({placeholders})")
        params.extend(lengths)
    if suffix:
        suffixes = split_filter_values(suffix)
        if suffixes:
            conditions.append("(" + " OR ".join("lower(d.domain) LIKE ?" for _ in suffixes) + ")")
            params.extend(f"%{item}" for item in suffixes)
    if start_date:
        ensure_date_format(start_date)
        conditions.append("joined_at >= ?")
        params.append(start_date)
    if end_date:
        ensure_date_format(end_date)
        conditions.append("joined_at <= ?")
        params.append(end_date)
    if include_chars:
        conditions.append("lower(d.domain) LIKE ?")
        params.append(f"%{include_chars.strip().lower()}%")
    if exclude_chars:
        conditions.append("lower(d.domain) NOT LIKE ?")
        params.append(f"%{exclude_chars.strip().lower()}%")
    for field, value in (("c.deletion_status", deletion_status), ("COALESCE(c.wechat_status, '否')", wechat_status),
                         ("COALESCE(c.qq_status, '否')", qq_status), ("COALESCE(c.pollution_status, '否')", pollution_status),
                         ("COALESCE(c.blocked_status, '否')", blocked_status), ("COALESCE(c.blacklist_status, '否')", blacklist_status),
                         ("c.filing_nature", filing_nature)):
        if value:
            conditions.append(f"{field} = ?")
            params.append(value)
    if filing_info:
        conditions.append("c.filing_info LIKE ?")
        params.append(f"%{filing_info.strip()}%")
    for field, start, end in (("c.creation_date", registration_start, registration_end),
                              ("c.expiration_date", expiration_start, expiration_end),
                              ("COALESCE(NULLIF(d.query_time, ''), c.checked_at, '')", query_start, query_end)):
        is_query_time = "query_time" in field
        if start:
            ensure_date_format(start)
            conditions.append(f"substr({field}, 1, 10) >= ?" if is_query_time else f"{field} >= ?")
            params.append(start)
        if end:
            ensure_date_format(end)
            conditions.append(f"substr({field}, 1, 10) <= ?" if is_query_time else f"{field} <= ?")
            params.append(end)

    where = " AND ".join(conditions)
    # 没有用到查询结果字段时，统计总数无需关联 domain_checks
    count_join = " LEFT JOIN domain_checks c ON c.domain = d.domain" if any("c." in item for item in conditions) else ""
    total = db.fetch_one(f"SELECT COUNT(*) AS total FROM domains d{count_join} WHERE {where}", tuple(params))["total"]
    stats = None
    if with_stats:
        # domain_checks 远小于 domains，用 CROSS JOIN 固定由它驱动连接；未查询的域名单独计数
        select = ", ".join(f"COALESCE(c.{field}, '{STAT_DEFAULTS[field]}') AS {field}" for field in STAT_FIELDS)
        stats = build_list_stats(
            f"SELECT {select}, COUNT(*) AS count FROM domain_checks c CROSS JOIN domains d ON d.domain = c.domain "
            f"WHERE {where} {STAT_GROUP_BY}",
            f"SELECT d.source_name AS label, COUNT(*) AS count FROM domains d{count_join} WHERE {where} "
            "GROUP BY d.source_name ORDER BY count DESC",
            tuple(params),
            f"SELECT COUNT(*) AS count FROM domains d LEFT JOIN domain_checks c ON c.domain = d.domain "
            f"WHERE {where} AND c.domain IS NULL",
        )
    params.extend([page_size, (page - 1) * page_size])
    rows = db.fetch_all(
        f"""
        SELECT d.domain, COALESCE(c.deletion_status, '') AS deletion_status,
               COALESCE(c.creation_date, '') AS creation_date,
               COALESCE(c.expiration_date, '') AS expiration_date,
               COALESCE(c.wechat_status, '否') AS wechat_status,
               COALESCE(c.qq_status, '否') AS qq_status,
               COALESCE(c.pollution_status, '否') AS pollution_status,
               COALESCE(c.blocked_status, '否') AS blocked_status,
               COALESCE(c.blacklist_status, '否') AS blacklist_status,
               COALESCE(c.filing_nature, '') AS filing_nature,
               COALESCE(c.filing_info, '') AS filing_info, d.source_name AS source,
               d.joined_at, COALESCE(NULLIF(d.query_time, ''), c.checked_at, '') AS query_time
        FROM domains d
        LEFT JOIN domain_checks c ON c.domain = d.domain
        WHERE {where}
        ORDER BY d.joined_at DESC, d.domain
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    )
    data: dict[str, Any] = {"records": rows, "current": page, "size": page_size, "total": total}
    if stats is not None:
        data["stats"] = stats
    return ok(data)


@app.post("/api/domains/generated")
def import_generated_domains(payload: GeneratedDomainsPayload) -> dict[str, Any]:
    """将生成器结果作为本地数据源写入域名列表。"""
    source = db.fetch_one("SELECT * FROM sources WHERE source_key = ?", (GENERATED_SOURCE_KEY,))
    if source is None:
        now = db.utc_now()
        source_id = db.execute(
            """
            INSERT INTO sources (source_key, name, adapter, base_url, page_path, download_template,
                                 parser, cookie_ciphertext, enabled, created_at, updated_at)
            VALUES (?, '域名生成', 'generic', '', '', '', 'line', '', 1, ?, ?)
            """,
            (GENERATED_SOURCE_KEY, now, now),
        )
        source = db.fetch_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    inserted, updated = db.upsert_domains(source or {}, datetime.now().date().isoformat(), set(payload.domains))
    return ok({"inserted": inserted, "updated": updated, "total": len(payload.domains)})


@app.get("/api/domains/suffixes")
def list_domain_suffixes() -> dict[str, Any]:
    global _suffix_cache
    cached_at = time.monotonic()
    if _suffix_cache and cached_at - _suffix_cache[0] < _SUFFIX_CACHE_TTL:
        return ok(_suffix_cache[1])
    rows = db.fetch_all(
        """
        SELECT suffix, COUNT(*) AS count
        FROM domains
        GROUP BY suffix
        ORDER BY suffix
        """
    )
    for row in rows:
        suffix = row["suffix"]
        if any(ord(char) > 127 for char in suffix):
            row["group"] = "chinese"
        elif len(suffix) == 2:
            row["group"] = "two"
        elif len(suffix) == 3:
            row["group"] = "three"
        elif len(suffix) == 4:
            row["group"] = "four"
        else:
            row["group"] = "other"
    _suffix_cache = (cached_at, rows)
    return ok(rows)


@app.get("/api/query-tasks")
def list_query_tasks() -> dict[str, Any]:
    # 页面只需要当前任务状态，不返回历史任务列表。
    row = db.fetch_one(
        "SELECT * FROM query_tasks WHERE status IN ('created', 'running') ORDER BY id DESC LIMIT 1"
    )
    if row is None:
        row = db.fetch_one("SELECT * FROM query_tasks ORDER BY id DESC LIMIT 1")
    return ok([public_query_task(row)] if row else [])


@app.get("/api/query-settings")
def get_query_settings() -> dict[str, Any]:
    row = db.fetch_one("SELECT settings_json, updated_at FROM query_settings WHERE id = 1")
    if row is None:
        return ok(None)
    try:
        settings = json.loads(row["settings_json"])
    except json.JSONDecodeError:
        return ok(None)
    settings["domain_composition"] = db.normalize_composition(settings.get("domain_composition"))
    return ok({"settings": settings, "updated_at": row["updated_at"]})


@app.put("/api/query-settings")
def save_query_settings(payload: QueryTaskPayload) -> dict[str, Any]:
    updated_at = db.utc_now()
    settings = payload.model_dump()
    db.execute(
        """
        INSERT INTO query_settings (id, settings_json, updated_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET settings_json = excluded.settings_json, updated_at = excluded.updated_at
        """,
        (json.dumps(settings, ensure_ascii=False), updated_at),
    )
    return ok({"settings": settings, "updated_at": updated_at})


@app.get("/api/query-settings/intercept-key")
def get_intercept_key() -> dict[str, Any]:
    return ok({"configured": boce_client.has_api_key()})


@app.put("/api/query-settings/intercept-key")
def save_intercept_key(payload: InterceptKeyPayload) -> dict[str, Any]:
    # 密钥加密保存，接口只返回是否已配置，不回显明文
    boce_client.save_api_key(payload.api_key)
    return ok({"configured": boce_client.has_api_key()})


@app.post("/api/query-tasks/preview")
def preview_query_task(payload: QueryPreviewPayload) -> dict[str, Any]:
    return ok(
        {
            "total": query_manager.preview(
                {
                    "lengths": payload.lengths,
                    "suffixes": payload.suffixes,
                    "exclude_chars": payload.exclude_chars,
                    "domain_composition": payload.domain_composition,
                }
            )
        }
    )


@app.post("/api/query-tasks")
def create_query_task(payload: QueryTaskPayload) -> dict[str, Any]:
    try:
        task_id = query_manager.start(
            {
                "name": "域名查询任务",
                "filters": {
                    "lengths": payload.lengths,
                    "suffixes": payload.suffixes,
                    "exclude_chars": payload.exclude_chars,
                    "domain_composition": payload.domain_composition,
                    "delete_type": payload.delete_type,
                    "expiration_start": payload.expiration_start,
                    "expiration_end": payload.expiration_end,
                    "registration_start": payload.registration_start,
                    "registration_end": payload.registration_end,
                    "exceptions": payload.exceptions.model_dump(),
                    "intercept_checks": payload.intercept_checks,
                },
                "threads": payload.threads,
                "whois_retries": payload.whois_retries,
                "icp_retries": payload.icp_retries,
                "qq_retries": payload.qq_retries,
                "wechat_retries": payload.wechat_retries,
                "douyin_retries": payload.douyin_retries,
                "blocked_retries": payload.blocked_retries,
                "pollution_retries": payload.pollution_retries,
                "blacklist_retries": payload.blacklist_retries,
                "continuous": payload.continuous,
                "proxy": {
                    "mode": payload.proxy_mode,
                    "endpoint": payload.proxy_endpoint,
                    "max_requests": payload.proxy_max_requests,
                    "stages": payload.proxy_stages,
                },
            }
        )
    except RuntimeError as exc:
        raise fail(str(exc), 409) from exc
    return ok(public_query_task(db.fetch_one("SELECT * FROM query_tasks WHERE id = ?", (task_id,)) or {}))


@app.post("/api/query-tasks/{task_id}/stop")
def stop_query_task(task_id: int) -> dict[str, Any]:
    task = db.fetch_one("SELECT id, status FROM query_tasks WHERE id = ?", (task_id,))
    if task is None:
        raise fail("查询任务不存在", 404)
    query_manager.stop(task_id)
    return ok(public_query_task(db.fetch_one("SELECT * FROM query_tasks WHERE id = ?", (task_id,)) or {}))


@app.get("/api/query-results")
def list_query_results(
    result: Literal["qualified", "unqualified"] = "qualified",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    domain: str | None = None,
    domain_composition: str | None = None,
    length: str | None = None,
    suffix: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    deletion_status: str | None = None,
    registration_start: str | None = None,
    registration_end: str | None = None,
    expiration_start: str | None = None,
    expiration_end: str | None = None,
    joined_start: str | None = None,
    joined_end: str | None = None,
    query_start: str | None = None,
    query_end: str | None = None,
    qq_status: str | None = None,
    wechat_status: str | None = None,
    blocked_status: str | None = None,
    pollution_status: str | None = None,
    blacklist_status: str | None = None,
    filing_nature: str | None = None,
    filing_info: str | None = None,
    include_chars: str | None = None,
    exclude_chars: str | None = None,
    with_stats: bool = False,
) -> dict[str, Any]:
    conditions = ["c.result = ?"]
    params: list[Any] = [result]
    if domain:
        conditions.append("c.domain LIKE ?")
        params.append(f"%{domain.strip()}%")
    if composition := parse_composition_filter(domain_composition):
        label_expression = "substr(c.domain, 1, length(c.domain) - instr(reverse(c.domain), '.'))"
        condition, kinds = db.label_kind_condition(composition, f"label_kind_of({label_expression})")
        conditions.append(condition)
        params.extend(kinds)
    lengths = parse_length_filter(length)
    if lengths:
        placeholders = ','.join('?' for _ in lengths)
        label_expression = "substr(c.domain, 1, length(c.domain) - instr(reverse(c.domain), '.'))"
        conditions.append(f"length({label_expression}) IN ({placeholders})")
        params.extend(lengths)
    if suffix:
        suffixes = split_filter_values(suffix)
        if suffixes:
            conditions.append("(" + " OR ".join("lower(c.domain) LIKE ?" for _ in suffixes) + ")")
            params.extend(f"%.{item}" for item in suffixes)
    if source:
        conditions.append("d.source_name LIKE ?")
        params.append(f"%{source.strip()}%")
    if keyword:
        value = f"%{keyword.strip()}%"
        keyword_fields = [
            "c.domain",
            "d.source_name",
            "c.deletion_status",
            "c.creation_date",
            "c.expiration_date",
            "c.qq_status",
            "c.wechat_status",
            "c.pollution_status",
            "c.blocked_status",
            "c.blacklist_status",
            "c.filing_nature",
            "c.filing_info",
            "c.reason",
            "c.checked_at"
        ]
        conditions.append(f"({ ' OR '.join(f'{field} LIKE ?' for field in keyword_fields) })")
        params.extend([value] * len(keyword_fields))
    if deletion_status:
        conditions.append("c.deletion_status = ?")
        params.append(deletion_status)
    for field, operator, value in (
        ("creation_date", ">=", registration_start),
        ("creation_date", "<=", registration_end),
        ("expiration_date", ">=", expiration_start),
        ("expiration_date", "<=", expiration_end),
    ):
        if value:
            ensure_date_format(value)
            conditions.append(f"c.{field} {operator} ?")
            params.append(value)
    for field, operator, value in (
        ("d.joined_at", ">=", joined_start),
        ("d.joined_at", "<=", joined_end),
        ("COALESCE(NULLIF(d.query_time, ''), c.checked_at, '')", ">=", query_start),
        ("COALESCE(NULLIF(d.query_time, ''), c.checked_at, '')", "<=", query_end),
    ):
        if value:
            ensure_date_format(value)
            conditions.append(f"substr({field}, 1, 10) {operator} ?")
            params.append(value)
    for field, value in (
        ("qq_status", qq_status),
        ("wechat_status", wechat_status),
        ("pollution_status", pollution_status),
        ("blocked_status", blocked_status),
        ("blacklist_status", blacklist_status),
    ):
        if value:
            conditions.append(f"c.{field} = ?")
            params.append(value)
    if filing_nature:
        conditions.append("c.filing_nature = ?")
        params.append(filing_nature)
    if filing_info:
        conditions.append("c.filing_info LIKE ?")
        params.append(f"%{filing_info.strip()}%")
    if include_chars:
        conditions.append("LOWER(c.domain) LIKE ?")
        params.append(f"%{include_chars.strip().lower()}%")
    if exclude_chars:
        conditions.append("LOWER(c.domain) NOT LIKE ?")
        params.append(f"%{exclude_chars.strip().lower()}%")
    where = " AND ".join(conditions)
    total_row = db.fetch_one(
        f"SELECT COUNT(*) AS total FROM domain_checks c JOIN domains d ON d.domain = c.domain WHERE {where}", tuple(params)
    )
    rows = db.fetch_all(
        f"""
        SELECT c.domain, d.source_name AS source, d.joined_at, d.query_time,
               c.result, c.reason, c.deletion_status, c.whois_status, c.expiration_date,
               c.creation_date, c.icp_found, c.qq_status, c.wechat_status,
               c.pollution_status, c.blocked_status, c.blacklist_status,
               c.filing_nature, c.filing_info, c.checked_at
        FROM domain_checks c JOIN domains d ON d.domain = c.domain
        WHERE {where}
        ORDER BY c.checked_at DESC, c.domain
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, (page - 1) * page_size),
    )
    data: dict[str, Any] = {"records": rows, "current": page, "size": page_size, "total": total_row["total"]}
    if with_stats:
        joins = "FROM domain_checks c CROSS JOIN domains d ON d.domain = c.domain"
        data["stats"] = build_list_stats(
            f"SELECT {', '.join(f'c.{field}' for field in STAT_FIELDS)}, COUNT(*) AS count {joins} WHERE {where} {STAT_GROUP_BY}",
            f"SELECT d.source_name AS label, COUNT(*) AS count {joins} WHERE {where} GROUP BY d.source_name ORDER BY count DESC",
            tuple(params),
        )
    return ok(data)


@app.get("/api/query-logs")
def list_query_logs(
    task_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=500, ge=1, le=500),
) -> dict[str, Any]:
    condition = "1 = 1"
    params: list[Any] = []
    if task_id is not None:
        condition = "task_id = ?"
        params.append(task_id)
    rows = db.fetch_all(
        f"SELECT id, task_id, created_at, level, stage, domain, message, detail "
        f"FROM query_logs WHERE {condition} ORDER BY id DESC LIMIT ?",
        (*params, limit),
    )
    rows.reverse()
    return ok(rows)


@app.delete("/api/query-results/{result}")
def clear_query_results(result: Literal["qualified", "unqualified"]) -> dict[str, Any]:
    running = db.fetch_one(
        "SELECT id FROM query_tasks WHERE status IN ('created', 'running') LIMIT 1"
    )
    if running:
        raise fail("请先停止正在运行的查询任务", 409)
    row = db.fetch_one("SELECT COUNT(*) AS total FROM domain_checks WHERE result = ?", (result,))
    deleted = int(row["total"] if row else 0)
    db.execute("DELETE FROM domain_checks WHERE result = ?", (result,))
    return ok({"deleted": deleted})


@app.post("/api/domains/reset-query-time")
def reset_domain_query_time() -> dict[str, Any]:
    running = db.fetch_one(
        "SELECT id FROM query_tasks WHERE status IN ('created', 'running') LIMIT 1"
    )
    if running:
        raise fail("请先停止正在运行的查询任务", 409)
    row = db.fetch_one("SELECT COUNT(*) AS total FROM domains WHERE query_time != ''", ())
    checks = db.fetch_one("SELECT COUNT(*) AS total FROM domain_checks", ())
    reset_count = int(row["total"] if row else 0)
    db.execute("UPDATE domains SET query_time = '' WHERE query_time != ''")
    db.execute("DELETE FROM domain_checks")
    return ok({"reset": reset_count, "cleared_results": int(checks["total"] if checks else 0)})


@app.get("/api/schedules")
def list_schedules() -> dict[str, Any]:
    return ok(
        [
            public_schedule(row)
            for row in db.fetch_all(
            """
            SELECT schedules.*, sources.name AS source_name, sources.source_key
            FROM schedules JOIN sources ON sources.id = schedules.source_id
            ORDER BY schedules.id
            """
            )
        ]
    )


@app.post("/api/schedules")
def save_schedule(payload: SchedulePayload) -> dict[str, Any]:
    get_source(payload.source_id)
    now = db.utc_now()
    existing = db.fetch_one("SELECT id FROM schedules WHERE source_id = ?", (payload.source_id,))
    if existing:
        schedule_id = existing["id"]
        db.execute(
            "UPDATE schedules SET name = ?, run_time = ?, suffixes_json = ?, enabled = ?, updated_at = ? WHERE id = ?",
            (payload.name, payload.run_time, json.dumps(payload.suffixes), int(payload.enabled), now, schedule_id),
        )
    else:
        schedule_id = db.execute(
            """
            INSERT INTO schedules (source_id, name, run_time, suffixes_json, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (payload.source_id, payload.name, payload.run_time, json.dumps(payload.suffixes), int(payload.enabled), now, now),
        )
    return ok(public_schedule(db.fetch_one("SELECT * FROM schedules WHERE id = ?", (schedule_id,)) or {}))


@app.put("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: int, payload: SchedulePayload) -> dict[str, Any]:
    schedule = db.fetch_one("SELECT id FROM schedules WHERE id = ?", (schedule_id,))
    if schedule is None:
        raise fail("定时任务不存在", 404)
    get_source(payload.source_id)
    conflict = db.fetch_one(
        "SELECT id FROM schedules WHERE source_id = ? AND id != ?",
        (payload.source_id, schedule_id),
    )
    if conflict:
        raise fail("该数据源已有定时任务，请先编辑或删除原任务", 409)
    db.execute(
        "UPDATE schedules SET source_id = ?, name = ?, run_time = ?, suffixes_json = ?, enabled = ?, updated_at = ? WHERE id = ?",
        (
            payload.source_id,
            payload.name,
            payload.run_time,
            json.dumps(payload.suffixes),
            int(payload.enabled),
            db.utc_now(),
            schedule_id,
        ),
    )
    return ok(public_schedule(db.fetch_one("SELECT * FROM schedules WHERE id = ?", (schedule_id,)) or {}))


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int) -> dict[str, Any]:
    schedule = db.fetch_one("SELECT id FROM schedules WHERE id = ?", (schedule_id,))
    if schedule is None:
        raise fail("定时任务不存在", 404)
    db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return ok({"deleted": 1})


@app.post("/api/schedules/{schedule_id}/run")
def run_schedule(schedule_id: int) -> dict[str, Any]:
    schedule = db.fetch_one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
    if schedule is None:
        raise fail("定时任务不存在", 404)
    today = date.today().isoformat()
    existing = db.fetch_one(
        """
        SELECT id, status FROM import_runs
        WHERE source_id = ? AND started_at LIKE ? AND trigger_type IN ('schedule', 'manual-schedule')
        ORDER BY id DESC LIMIT 1
        """,
        (schedule["source_id"], f"{today}%"),
    )
    if existing and existing["status"] in {"running", "success"}:
        return ok({"run_id": existing["id"], "reused": True})
    db.claim_schedule(schedule_id, today)
    try:
        source = get_source(schedule["source_id"])
        suffixes = json.loads(schedule.get("suffixes_json") or "[]")
        if source.get("adapter") == "west_cn":
            run_ids = queue_west_suffixes(schedule["source_id"], suffixes, today, "manual-schedule")
            return ok({"run_id": run_ids[0], "run_ids": run_ids})
        run_id = queue_latest(schedule["source_id"], "txt", "manual-schedule")
    except SourceError as exc:
        raise fail(str(exc), 422) from exc
    return ok({"run_id": run_id})


# 生产环境由后端直接提供前端构建产物（Docker 镜像中位于 /app/dist）；开发时 dist 不存在，由 vite 提供页面
DIST_DIR = db.BASE_DIR / "dist"

if DIST_DIR.is_dir():

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        file = (DIST_DIR / path).resolve()
        if path and file.is_file() and file.is_relative_to(DIST_DIR.resolve()):
            # 带哈希的静态资源可长期缓存；index.html 必须每次重新获取，否则更新后仍加载旧页面
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        # history 路由：未知路径都返回 index.html
        return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})
