"""控制台统计：重统计（全表聚合）缓存数分钟，运行状态与动态每次实时读取。"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from . import db

router = APIRouter(prefix="/api")

_STATS_TTL = 300.0
_TREND_DAYS = 14
_stats_cache: tuple[float, dict[str, Any]] | None = None
_stats_lock = threading.Lock()

# label_kind 为字符类别代码组合，如 an 表示英文+数字
LABEL_KIND_NAMES = {"a": "英文", "n": "数字", "an": "英数", "c": "中文"}


def label_kind_name(kind: str) -> str:
    return LABEL_KIND_NAMES.get(kind) or ("含符号" if "s" in kind else "其他")


def _merge_kinds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, int] = {}
    for row in rows:
        name = label_kind_name(row["kind"])
        merged[name] = merged.get(name, 0) + row["value"]
    return [{"name": name, "value": value} for name, value in sorted(merged.items(), key=lambda item: -item[1])]
LENGTH_BUCKETS = [(1, 3, "1-3"), (4, 4, "4"), (5, 5, "5"), (6, 6, "6"), (7, 7, "7"), (8, 8, "8"), (9, 10, "9-10")]
STATUS_FIELDS = [
    ("wechat_status", "微信拦截"),
    ("qq_status", "QQ 拦截"),
    ("pollution_status", "污染"),
    ("blocked_status", "拦截"),
    ("blacklist_status", "黑名单"),
]
RUN_TRIGGER_NAMES = {
    "manual": "手动采集",
    "manual-file": "文件导入",
    "manual-schedule": "手动执行定时任务",
    "manual-west": "手动采集",
    "schedule": "定时采集",
}


def ok(data: Any) -> dict[str, Any]:
    return {"code": "0000", "data": data, "msg": ""}


def _ratio(part: int, total: int) -> float:
    return round(part * 100 / total, 2) if total else 0.0


def _heavy_stats() -> dict[str, Any]:
    today = date.today()
    trend_start = (today - timedelta(days=_TREND_DAYS - 1)).isoformat()
    # 只读聚合耗时数秒，不占用全局写锁，避免阻塞查询、监控任务写库
    with db.get_connection() as connection:

        def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

        total = connection.execute("SELECT COUNT(*) FROM domains").fetchone()[0]
        queried = connection.execute("SELECT COUNT(*) FROM domains WHERE query_time <> ''").fetchone()[0]
        by_source = rows(
            "SELECT source_name AS name, COUNT(*) AS value FROM domains GROUP BY source_id ORDER BY value DESC"
        )
        by_kind = rows("SELECT label_kind AS kind, COUNT(*) AS value FROM domains GROUP BY label_kind ORDER BY value DESC")
        by_length = rows("SELECT label_length AS length, COUNT(*) AS value FROM domains GROUP BY label_length")
        suffix_rows = rows("SELECT suffix AS name, COUNT(*) AS value FROM domains GROUP BY suffix ORDER BY value DESC")
        daily = rows(
            """
            SELECT joined_at AS day, source_name AS source, COUNT(*) AS value
            FROM domains WHERE joined_at >= ? GROUP BY joined_at, source_id
            """,
            (trend_start,),
        )

    days = [(today - timedelta(days=offset)).isoformat() for offset in range(_TREND_DAYS - 1, -1, -1)]
    sources = [item["name"] for item in by_source]
    daily_map = {(item["day"], item["source"]): item["value"] for item in daily}
    today_str, yesterday_str = days[-1], days[-2]

    buckets = [
        {"name": label, "value": sum(r["value"] for r in by_length if low <= r["length"] <= high)}
        for low, high, label in LENGTH_BUCKETS
    ]
    buckets.append({"name": "11+", "value": sum(r["value"] for r in by_length if r["length"] > 10)})

    top_suffixes = suffix_rows[:10]
    others = sum(item["value"] for item in suffix_rows[10:])
    if others:
        top_suffixes.append({"name": "其他", "value": others})

    return {
        "total": total,
        "queried": queried,
        "pending": total - queried,
        "queryRate": _ratio(queried, total),
        "suffixCount": len(suffix_rows),
        "todayJoined": sum(daily_map.get((today_str, s), 0) for s in sources),
        "yesterdayJoined": sum(daily_map.get((yesterday_str, s), 0) for s in sources),
        "bySource": by_source,
        "byKind": _merge_kinds(by_kind),
        "byLength": buckets,
        "bySuffix": top_suffixes,
        "trend": {
            "days": days,
            "series": [{"name": s, "data": [daily_map.get((d, s), 0) for d in days]} for s in sources],
        },
    }


def _rebuild_stats() -> dict[str, Any]:
    global _stats_cache
    with _stats_lock:
        _stats_cache = (time.monotonic(), {**_heavy_stats(), "generatedAt": db.utc_now()})
        return _stats_cache[1]


def _background_rebuild() -> None:
    # 已有线程在重算时直接跳过
    if _stats_lock.acquire(blocking=False):
        _stats_lock.release()
        threading.Thread(target=_rebuild_stats, daemon=True).start()


def _cached_stats(refresh: bool) -> dict[str, Any]:
    """过期后先返回旧数据并在后台重算，页面无需等待全表聚合。"""
    cache = _stats_cache
    if refresh or cache is None:
        return _rebuild_stats()
    if time.monotonic() - cache[0] > _STATS_TTL:
        _background_rebuild()
    return cache[1]


def warm_up() -> None:
    threading.Thread(target=_rebuild_stats, daemon=True).start()


def _check_stats() -> dict[str, Any]:
    results = {row["result"]: row["value"] for row in db.fetch_all(
        "SELECT result, COUNT(*) AS value FROM domain_checks GROUP BY result"
    )}
    total = sum(results.values())
    status_sql = ", ".join(f"SUM({field} = '是') AS {field}" for field, _ in STATUS_FIELDS)
    status_row = db.fetch_one(f"SELECT {status_sql} FROM domain_checks") or {}
    today_row = db.fetch_one(
        "SELECT COUNT(*) AS checked, SUM(result = 'qualified') AS qualified FROM domain_checks WHERE checked_at >= ?",
        (date.today().isoformat(),),
    ) or {}

    def grouped(column: str, where: str = "") -> list[dict[str, Any]]:
        return db.fetch_all(
            f"""
            SELECT COALESCE(NULLIF({column}, ''), '未知') AS name, COUNT(*) AS value
            FROM domain_checks {where} GROUP BY 1 ORDER BY value DESC LIMIT 8
            """
        )

    return {
        "total": total,
        "qualified": results.get("qualified", 0),
        "unqualified": results.get("unqualified", 0),
        "kicked": results.get("kicked", 0),
        "qualifiedRate": _ratio(results.get("qualified", 0), total),
        "todayChecked": today_row.get("checked") or 0,
        "todayQualified": today_row.get("qualified") or 0,
        "reasons": grouped("reason", "WHERE result <> 'qualified'"),
        "deletionStatus": grouped("deletion_status"),
        "filingNature": grouped("filing_nature"),
        "risks": [{"name": label, "value": status_row.get(field) or 0} for field, label in STATUS_FIELDS],
        "latestQualified": db.fetch_all(
            """
            SELECT domain, expiration_date, deletion_status, filing_nature, checked_at
            FROM domain_checks WHERE result = 'qualified' ORDER BY checked_at DESC LIMIT 8
            """
        ),
    }


def _next_run(run_time: str, last_run_key: str) -> str:
    now = datetime.now()
    hour, minute = (int(part) for part in run_time.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now or last_run_key == now.strftime("%Y-%m-%d"):
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M")


QUERY_TASK_FIELDS = (
    "id", "name", "status", "threads", "continuous", "total_count", "processed_count",
    "qualified_count", "unqualified_count", "current_domain", "error", "started_at", "finished_at",
)


def _runtime() -> dict[str, Any]:
    task = db.fetch_one(
        "SELECT * FROM query_tasks WHERE status IN ('created', 'running') ORDER BY id DESC LIMIT 1"
    ) or db.fetch_one("SELECT * FROM query_tasks ORDER BY id DESC LIMIT 1")
    schedules = db.fetch_all(
        """
        SELECT s.id, s.name, s.run_time, s.enabled, s.last_run_key, s.last_run_at, src.name AS source_name
        FROM schedules s JOIN sources src ON src.id = s.source_id ORDER BY s.run_time, s.id
        """
    )
    for item in schedules:
        item["next_run"] = _next_run(item["run_time"], item["last_run_key"] or "") if item["enabled"] else ""
    today_runs = db.fetch_one(
        """
        SELECT COUNT(*) AS runs, COALESCE(SUM(inserted_count), 0) AS inserted, COALESCE(SUM(total_count), 0) AS total
        FROM import_runs WHERE requested_date = ? AND status = 'success'
        """,
        (date.today().isoformat(),),
    ) or {}
    latest_run = db.fetch_one(
        """
        SELECT r.id, r.status, r.trigger_type, r.total_count, r.inserted_count, r.error, r.started_at, r.finished_at,
               s.name AS source_name
        FROM import_runs r LEFT JOIN sources s ON s.id = r.source_id ORDER BY r.id DESC LIMIT 1
        """
    )
    monitor = db.fetch_one("SELECT * FROM monitor_state WHERE id = 1") or {}
    apis = db.fetch_one("SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled FROM registrar_apis") or {}
    sources = db.fetch_all(
        """
        SELECT id, name, enabled, cookie_ciphertext <> '' AS has_cookie FROM sources
        WHERE deleted_at = '' AND source_key != 'generated' ORDER BY id
        """
    )

    def counts(sql: str) -> dict[str, int]:
        return {row["status"]: row["value"] for row in db.fetch_all(sql)}

    return {
        "queryTask": {key: task[key] for key in QUERY_TASK_FIELDS} if task else None,
        "collect": {
            "schedules": schedules,
            "runs": counts("SELECT status, COUNT(*) AS value FROM import_runs GROUP BY status"),
            "todayRuns": today_runs.get("runs") or 0,
            "todayInserted": today_runs.get("inserted") or 0,
            "todayDownloaded": today_runs.get("total") or 0,
            "latest": latest_run,
        },
        "monitor": {
            "status": monitor.get("status", "stopped"),
            "checked": monitor.get("checked_count", 0),
            "available": monitor.get("available_count", 0),
            "registered": monitor.get("registered_count", 0),
            "kicked": monitor.get("kicked_count", 0),
            "currentDomain": monitor.get("current_domain", ""),
            "lastError": monitor.get("last_error", ""),
            "startedAt": monitor.get("started_at", ""),
            "domains": counts("SELECT status, COUNT(*) AS value FROM monitor_domain_state GROUP BY status"),
        },
        "registration": counts("SELECT status, COUNT(*) AS value FROM registration_attempts GROUP BY status"),
        "registrarApis": {"total": apis.get("total") or 0, "enabled": apis.get("enabled") or 0},
        "sources": [
            {"id": s["id"], "name": s["name"], "enabled": bool(s["enabled"]), "hasCookie": bool(s["has_cookie"])}
            for s in sources
        ],
    }


def _alerts(runtime: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    def add(level: str, title: str, desc: str, route: str) -> None:
        alerts.append({"level": level, "title": title, "desc": desc, "route": route})

    if not runtime["registrarApis"]["enabled"]:
        add("warning", "未配置可用的注册商接口", "监控发现可注册域名后无法自动提交注册", "runtime-control_monitor-tasks")
    for source in runtime["sources"]:
        if source["enabled"] and not source["hasCookie"]:
            add("warning", f"数据源「{source['name']}」未配置 Cookie", "采集可能因未登录而失败", "runtime-control_expired-collection")
    latest = runtime["collect"]["latest"]
    if latest and latest["status"] == "failure":
        name = latest.get("source_name") or "未知来源"
        add("error", f"最近一次采集失败（{name}）", (latest.get("error") or "")[:120], "runtime-control_expired-collection")
    task = runtime["queryTask"]
    if task and task["error"]:
        add("error", f"查询任务 #{task['id']} 出错", task["error"][:120], "runtime-control_query-tasks")
    if runtime["monitor"]["lastError"]:
        add("error", "监控任务出错", runtime["monitor"]["lastError"][:120], "runtime-control_monitor-tasks")
    return alerts


def _activities(limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    runs = db.fetch_all(
        """
        SELECT r.status, r.trigger_type, r.total_count, r.inserted_count, r.error,
               COALESCE(NULLIF(r.finished_at, ''), r.started_at) AS time, s.name AS source_name
        FROM import_runs r LEFT JOIN sources s ON s.id = r.source_id ORDER BY r.id DESC LIMIT ?
        """,
        (limit,),
    )
    for run in runs:
        trigger = RUN_TRIGGER_NAMES.get(run["trigger_type"], run["trigger_type"])
        if run["status"] == "success":
            message = f"{trigger}完成：下载 {run['total_count']} 条，新增 {run['inserted_count']} 条"
        elif run["status"] == "failure":
            message = f"{trigger}失败：{(run['error'] or '')[:80]}"
        else:
            message = f"{trigger}进行中"
        level = {"failure": "error", "success": "success"}.get(run["status"], "info")
        items.append({"time": run["time"], "type": "采集", "level": level, "title": run["source_name"] or "", "message": message})
    for table, kind in (("query_logs", "查询"), ("monitor_logs", "监控")):
        logs = db.fetch_all(
            f"SELECT created_at, level, domain, message FROM {table} WHERE level <> 'info' ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        for log in logs:
            items.append({
                "time": log["created_at"],
                "type": kind,
                "level": log["level"],
                "title": log["domain"] or "",
                "message": log["message"],
            })
    items.sort(key=lambda item: item["time"] or "", reverse=True)
    return items[:limit]


@router.get("/dashboard")
def get_dashboard(refresh: bool = Query(default=False)) -> dict[str, Any]:
    runtime = _runtime()
    return ok({
        "stats": _cached_stats(refresh),
        "checks": _check_stats(),
        "runtime": runtime,
        "alerts": _alerts(runtime),
        "activities": _activities(12),
    })


@router.get("/dashboard/runtime")
def get_dashboard_runtime() -> dict[str, Any]:
    runtime = _runtime()
    return ok({
        "checks": _check_stats(),
        "runtime": runtime,
        "alerts": _alerts(runtime),
        "activities": _activities(12),
    })
