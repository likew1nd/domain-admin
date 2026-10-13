from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date
from typing import Any

from . import db
from .crypto import decrypt_cookie
from .registrar_adapters import GnameRegistrar, RegistrarError

logger = logging.getLogger(__name__)

MONITOR_DOMAINS_SQL = """SELECT c.* FROM domain_checks c
    LEFT JOIN monitor_domain_state m ON m.domain = c.domain
    WHERE c.result = 'qualified'
      AND (m.status IS NULL OR m.status IN ('monitoring', 'available'))
      AND NOT EXISTS (SELECT 1 FROM registered_domains r WHERE r.domain = c.domain)"""


def booking_result(attempt: dict[str, Any]) -> str:
    status = attempt["status"]
    labels = {"success": "成功", "pending": "等待提交", "sending": "正在提交"}
    if status in labels:
        return labels[status]
    response = attempt.get("response") or ""
    detail = attempt.get("error") or response or "预订失败"
    if status == "uncertain":
        return f"待核实：{detail}" if detail != "预订失败" else "待核实，请到 Gname 核实订单"
    try:
        payload = json.loads(response)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        for key in ("msg", "message"):
            message = payload.get(key)
            if isinstance(message, str) and message.strip():
                return message.strip()
    return detail


def date_matches(value: Any, start: str | None, end: str | None) -> bool:
    text = str(value or "").strip().replace("/", "-")
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[ T])", text):
        return False
    try:
        parsed = date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return False
    return (not start or parsed >= start) and (not end or parsed <= end)


def filing_matches(context: dict[str, Any], scheme: dict[str, Any]) -> list[tuple[bool, str]]:
    start, end = scheme.get("filing_start"), scheme.get("filing_end")
    natures = scheme.get("filing_natures") or []
    if not (start or end or natures):
        return []
    data = context.get("icp_data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    params = data.get("params") if isinstance(data, dict) else None
    records = params.get("list") or [] if isinstance(params, dict) else []
    records = [item for item in records if isinstance(item, dict)]
    def checks(record: dict[str, Any]) -> list[tuple[bool, str]]:
        result = []
        if start or end:
            value = record.get("updateRecordTime") or record.get("approvalTime") or record.get("recordTime")
            result.append((date_matches(value, start, end), "备案时间"))
        if natures:
            nature = str(record.get("natureName") or record.get("mainUnitNature") or "").strip()
            result.append((nature in natures, "备案性质"))
        return result
    # 日期和性质必须来自同一备案记录。
    return [(any(all(hit for hit, _ in checks(record)) for record in records), "备案条件")]


def gname_account(api_id: int) -> tuple[dict[str, Any], GnameRegistrar]:
    api = db.fetch_one("SELECT * FROM registrar_apis WHERE id = ?", (api_id,))
    if not api or not api["enabled"] or api["adapter"] != "gname" or not api["token_ciphertext"]:
        raise RegistrarError("请选择已启用且已配置 AppID/AppKey 的 Gname 账号")
    return api, GnameRegistrar(api, decrypt_cookie(api["token_ciphertext"]))


class BookingManager:
    def __init__(self) -> None:
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def settings(self) -> dict[str, Any]:
        with db._db_lock:
            row = db.fetch_one("SELECT settings_json FROM booking_settings WHERE id = 1")
            if row:
                return json.loads(row["settings_json"])
            # 一次性迁移旧查询配置，之后预订仅使用监控任务的独立设置。
            old = db.fetch_one("SELECT settings_json FROM query_settings WHERE id = 1")
            settings = (json.loads(old["settings_json"]).get("booking") if old else None) or {
                "enabled": False, "schemes": []}
            return self.save_settings(settings)

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        db.execute("""INSERT INTO booking_settings (id, settings_json, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET settings_json = excluded.settings_json, updated_at = excluded.updated_at""",
                   (json.dumps(settings, ensure_ascii=False), db.utc_now()))
        self._wake.set()
        return settings

    @staticmethod
    def _matching_schemes(context: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
        from .query_service import QueryTaskManager

        label, _, suffix = context["domain"].lower().partition(".")
        return [scheme for scheme in settings.get("schemes") or [] if scheme.get("enabled")
                and QueryTaskManager._match_exception_scheme(label, suffix, scheme, context)[0]]

    def scan(self, settings: dict[str, Any]) -> None:
        if settings.get("enabled"):
            for context in db.fetch_all(MONITOR_DOMAINS_SQL):
                self._enqueue_context(context, settings)

    def enqueue(self, domain: str, settings: dict[str, Any]) -> None:
        if not settings.get("enabled"):
            return
        context = db.fetch_one(MONITOR_DOMAINS_SQL + " AND c.domain = ?", (domain,))
        if not context:
            return
        self._enqueue_context(context, settings)

    def _enqueue_context(self, context: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
        domain = context["domain"]
        with db._db_lock, db.get_connection() as connection:
            attempts = connection.execute("SELECT * FROM booking_attempts WHERE domain = ?", (domain,)).fetchall()
            # 成功或尚不能确认结果时，不再尝试其他通道；也清理旧版留下的未发送项。
            if any(row["status"] in {"success", "sending", "uncertain"} for row in attempts):
                connection.execute("DELETE FROM booking_attempts WHERE domain = ? AND status = 'pending'", (domain,))
                return None
            previous = {(row["registrar_api_id"], row["channel"]): row for row in attempts}
            for scheme in self._matching_schemes(context, settings):
                api_id = scheme.get("api_id", 0)
                for channel in scheme.get("channels") or []:
                    old = previous.get((api_id, channel))
                    if old and old["status"] == "failure":
                        continue
                    # 每个域名只保留当前轮到的通道，配置重排后也按最新顺序执行。
                    connection.execute("""DELETE FROM booking_attempts WHERE domain = ? AND status = 'pending'
                        AND NOT (registrar_api_id = ? AND channel = ?)""", (domain, api_id, channel))
                    api = connection.execute("SELECT name FROM registrar_apis WHERE id = ?", (api_id,)).fetchone()
                    connection.execute(
                        """INSERT OR IGNORE INTO booking_attempts
                           (domain, registrar_api_id, registrar_name, channel, scheme_name, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (domain, api_id, api["name"] if api else "已删除账号", channel,
                         scheme.get("name") or "预订方案", db.utc_now()),
                    )
                    return dict(connection.execute("""SELECT * FROM booking_attempts
                        WHERE domain = ? AND registrar_api_id = ? AND channel = ?""", (domain, api_id, channel)).fetchone())
            connection.execute("DELETE FROM booking_attempts WHERE domain = ? AND status = 'pending'", (domain,))
            return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        db.execute("UPDATE booking_attempts SET status = 'uncertain', error = ?, completed_at = ? WHERE status = 'sending'",
                   ("提交期间服务重启，请到 Gname 核实订单，系统不会自动重发", db.utc_now()))
        self.settings()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gname-booking", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=45)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                settings = self.settings()
                self.scan(settings)
                while not self._stop.is_set() and self.settings().get("enabled"):
                    attempt = db.fetch_one("SELECT * FROM booking_attempts WHERE status = 'pending' ORDER BY id LIMIT 1")
                    if not attempt:
                        break
                    self._submit(attempt)
            except Exception:
                logger.exception("预订队列处理失败")
            self._wake.wait(5)

    def _submit(self, attempt: dict[str, Any]) -> None:
        settings = self.settings()
        if not settings.get("enabled"):
            return
        current = db.fetch_one("SELECT status FROM booking_attempts WHERE id = ?", (attempt["id"],))
        if not current or current["status"] != "pending":
            return
        sent = False
        status, response, error = "failure", "", ""
        try:
            qualified = db.fetch_one(MONITOR_DOMAINS_SQL + " AND c.domain = ?", (attempt["domain"],))
            if not qualified:
                raise RegistrarError("域名已移出符合列表或已注册，取消预订")
            selected = self._enqueue_context(qualified, settings)
            if not selected or selected["id"] != attempt["id"]:
                return
            scheme = next((scheme for scheme in self._matching_schemes(qualified, settings)
                           if scheme.get("api_id") == attempt["registrar_api_id"]
                           and attempt["channel"] in (scheme.get("channels") or [])), None)
            if not scheme:
                # 当前规则不再匹配，撤销未发送项；以后重新命中可再次入队。
                db.execute("DELETE FROM booking_attempts WHERE id = ? AND status = 'pending'", (attempt["id"],))
                return
            _, adapter = gname_account(attempt["registrar_api_id"])
            channel = next((item for item in adapter.backorder_channels()
                            if str(item.get("channel")) == str(attempt["channel"])), None)
            suffix = attempt["domain"].partition(".")[2]
            if not channel or str(channel.get("status")) != "1":
                raise RegistrarError("所选 Gname 通道未开放")
            tlds = [str(value).lower().lstrip(".") for value in channel.get("tlds") or []]
            if suffix not in tlds:
                raise RegistrarError("所选 Gname 通道不支持该域名后缀")
            with db._db_lock, db.get_connection() as connection:
                latest = self.settings()
                if not latest.get("enabled"):
                    return
                qualified = db.fetch_one(MONITOR_DOMAINS_SQL + " AND c.domain = ?", (attempt["domain"],))
                selected = self._enqueue_context(qualified, latest) if qualified else None
                if not selected or selected["id"] != attempt["id"]:
                    db.execute("DELETE FROM booking_attempts WHERE id = ? AND status = 'pending'", (attempt["id"],))
                    return
                claimed = connection.execute(
                    "UPDATE booking_attempts SET status = 'sending' WHERE id = ? AND status = 'pending'",
                    (attempt["id"],),
                ).rowcount
            if not claimed:
                return
            sent = True
            db.execute("UPDATE booking_attempts SET scheme_name = ? WHERE id = ?", (scheme.get("name") or "预订方案", attempt["id"]))
            result = adapter.backorder(attempt["domain"], attempt["channel"], scheme.get("name") or "预订方案")
            status, response = ("success" if result.success else "failure"), result.response
            if not result.success:
                error = result.reason or booking_result({"status": status, "response": response})
        except Exception as exc:
            status, error = ("uncertain" if sent else "failure"), str(exc)
        db.execute("UPDATE booking_attempts SET status = ?, response = ?, error = ?, completed_at = ? WHERE id = ?",
                   (status, response, error, db.utc_now(), attempt["id"]))
        self.enqueue(attempt["domain"], self.settings())


booking_manager = BookingManager()
