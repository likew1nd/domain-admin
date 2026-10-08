from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from . import db
from .crypto import decrypt_cookie
from .registrar_adapters import RegistrarError, create_adapter
from .whois_client import deletion_status, is_available, lookup


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MonitorManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

    def settings(self) -> dict[str, Any]:
        row = db.fetch_one("SELECT * FROM monitor_settings WHERE id = 1")
        if row is None:
            return {
                "interval_seconds": 30,
                "concurrency": 5,
                "whois_retries": 2,
                "auto_register": False,
                "auto_start": False,
                "availability_api_id": 0,
                "api_ids": [],
            }
        try:
            api_ids = json.loads(row["api_ids_json"] or "[]")
        except json.JSONDecodeError:
            api_ids = []
        api_rows = db.fetch_all("SELECT id, enabled FROM registrar_apis")
        valid_ids = {int(api["id"]) for api in api_rows}
        enabled_ids = {int(api["id"]) for api in api_rows if api["enabled"]}
        return {
            "interval_seconds": int(row["interval_seconds"]),
            "concurrency": int(row["concurrency"]),
            "whois_retries": int(row["whois_retries"]),
            "auto_register": bool(row["auto_register"]),
            "auto_start": bool(row["auto_start"]),
            "availability_api_id": (
                int(row["availability_api_id"] or 0)
                if int(row["availability_api_id"] or 0) in enabled_ids
                else 0
            ),
            "api_ids": [int(value) for value in api_ids if str(value).isdigit() and int(value) in valid_ids],
            "updated_at": row["updated_at"],
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = {
            "interval_seconds": max(5, min(3600, int(payload.get("interval_seconds", 30) or 30))),
            "concurrency": max(1, min(50, int(payload.get("concurrency", 5) or 5))),
            "whois_retries": max(1, min(10, int(payload.get("whois_retries", 2) or 2))),
            "auto_register": int(bool(payload.get("auto_register", False))),
            "auto_start": int(bool(payload.get("auto_start", False))),
            "availability_api_id": max(0, int(payload.get("availability_api_id", 0) or 0)),
            "api_ids": sorted({int(value) for value in payload.get("api_ids", []) if str(value).isdigit()}),
        }
        updated_at = now()
        db.execute(
            """
            INSERT INTO monitor_settings (
                id, interval_seconds, concurrency, whois_retries, auto_register,
                auto_start, availability_api_id, api_ids_json, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                interval_seconds = excluded.interval_seconds,
                concurrency = excluded.concurrency,
                whois_retries = excluded.whois_retries,
                auto_register = excluded.auto_register,
                auto_start = excluded.auto_start,
                availability_api_id = excluded.availability_api_id,
                api_ids_json = excluded.api_ids_json,
                updated_at = excluded.updated_at
            """,
            (
                values["interval_seconds"],
                values["concurrency"],
                values["whois_retries"],
                values["auto_register"],
                values["auto_start"],
                values["availability_api_id"],
                json.dumps(values["api_ids"]),
                updated_at,
            ),
        )
        return self.settings()

    def status(self) -> dict[str, Any]:
        row = db.fetch_one("SELECT * FROM monitor_state WHERE id = 1") or {}
        return dict(row)

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            current_settings = self.settings()
            if not current_settings["availability_api_id"] or not self._selected_api(current_settings["availability_api_id"]):
                raise ValueError("请先在运行设置中选择一个可注册查询 API")
            event = threading.Event()
            self._stop_event = event
            timestamp = now()
            db.execute(
                """
                UPDATE monitor_state
                SET status = 'running', total_count = 0, checked_count = 0,
                    available_count = 0, registered_count = 0, kicked_count = 0,
                    current_domain = '', last_error = '', started_at = ?, updated_at = ?
                WHERE id = 1
                """,
                (timestamp, timestamp),
            )
            self._thread = threading.Thread(target=self._run, args=(event,), name="domain-monitor", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._stop_event and self._thread and self._thread.is_alive():
                self._stop_event.set()
                db.execute("UPDATE monitor_state SET status = 'stopping', updated_at = ? WHERE id = 1", (now(),))
            else:
                db.execute("UPDATE monitor_state SET status = 'stopped', current_domain = '', updated_at = ? WHERE id = 1", (now(),))
            return self.status()

    def resume_if_configured(self) -> None:
        if self.settings().get("auto_start"):
            self.start()

    def shutdown(self) -> None:
        self.stop()

    def logs(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = db.fetch_all(
            "SELECT id, created_at, level, stage, domain, message, detail FROM monitor_logs ORDER BY id DESC LIMIT ?",
            (max(1, min(500, limit)),),
        )
        rows.reverse()
        return rows

    def _run(self, event: threading.Event) -> None:
        try:
            while not event.is_set():
                settings = self.settings()
                self._run_cycle(settings, event)
                event.wait(settings["interval_seconds"])
        except Exception as exc:  # keep the monitor state visible after an unexpected failure
            self._log("error", "monitor", f"监控任务异常: {exc}")
            db.execute(
                "UPDATE monitor_state SET status = 'error', last_error = ?, updated_at = ? WHERE id = 1",
                (str(exc), now()),
            )
        finally:
            with self._lock:
                if self._stop_event is event:
                    self._stop_event = None
                    self._thread = None
            current = self.status()
            if current.get("status") == "stopping":
                db.execute("UPDATE monitor_state SET status = 'stopped', updated_at = ? WHERE id = 1", (now(),))

    def _run_cycle(self, settings: dict[str, Any], event: threading.Event) -> None:
        candidates = db.fetch_all(
            """
            SELECT c.domain
            FROM domain_checks c
            LEFT JOIN monitor_domain_state m ON m.domain = c.domain
            WHERE c.result = 'qualified'
              AND (m.status IS NULL OR m.status = 'monitoring' OR m.status = 'available')
            ORDER BY c.checked_at ASC, c.domain
            """
        )
        total = len(candidates)
        db.execute(
            "UPDATE monitor_state SET total_count = ?, checked_count = 0, available_count = 0, updated_at = ? WHERE id = 1",
            (total, now()),
        )
        if not candidates:
            self._log("info", "monitor", "当前没有待监控的符合域名")
            return

        api_configs = self._selected_apis(settings["api_ids"])
        availability_api = self._selected_api(settings["availability_api_id"])
        if not availability_api:
            message = "未找到可用的可注册查询 API，监控等待配置"
            db.execute("UPDATE monitor_state SET last_error = ?, updated_at = ? WHERE id = 1", (message, now()))
            self._log("error", "availability", message)
            return
        with ThreadPoolExecutor(max_workers=settings["concurrency"], thread_name_prefix="domain-watch") as pool:
            futures = {
                pool.submit(
                    self._check_domain,
                    str(row["domain"]),
                    settings,
                    api_configs,
                    availability_api,
                    event,
                ): str(row["domain"])
                for row in candidates
            }
            for future in as_completed(futures):
                if event.is_set():
                    break
                try:
                    result = future.result()
                    if result == "available":
                        db.execute(
                            "UPDATE monitor_state SET available_count = available_count + 1, checked_count = checked_count + 1, current_domain = '', updated_at = ? WHERE id = 1",
                            (now(),),
                        )
                    elif result == "registered":
                        db.execute(
                            "UPDATE monitor_state SET registered_count = registered_count + 1, checked_count = checked_count + 1, current_domain = '', updated_at = ? WHERE id = 1",
                            (now(),),
                        )
                    elif result == "kicked":
                        db.execute(
                            "UPDATE monitor_state SET kicked_count = kicked_count + 1, checked_count = checked_count + 1, current_domain = '', updated_at = ? WHERE id = 1",
                            (now(),),
                        )
                    else:
                        db.execute(
                            "UPDATE monitor_state SET checked_count = checked_count + 1, current_domain = '', updated_at = ? WHERE id = 1",
                            (now(),),
                        )
                except Exception as exc:
                    self._log("error", "monitor", f"处理域名失败: {exc}", futures[future])

    def _check_domain(
        self,
        domain: str,
        settings: dict[str, Any],
        api_configs: list[dict[str, Any]],
        availability_api: dict[str, Any] | None,
        event: threading.Event,
    ) -> str:
        if event.is_set():
            return "stopped"
        db.execute("UPDATE monitor_state SET current_domain = ?, updated_at = ? WHERE id = 1", (domain, now()))
        if availability_api:
            try:
                self._log("info", "availability", f"使用 {availability_api['name']} 查询可注册状态", domain)
                adapter = create_adapter(availability_api, decrypt_cookie(availability_api.get("token_ciphertext", "")))
                availability = adapter.check_available(domain)
                if availability.available is None:
                    reason = availability.reason or f"HTTP {availability.status_code}"
                    self._save_domain_state(domain, "monitoring", reason)
                    self._log("warning", "availability", f"可注册查询未得到确定结果：{reason}", domain, availability.response)
                    return "error"
                if availability.available:
                    self._save_domain_state(domain, "available", "")
                    self._log("info", "availability", f"{availability_api['name']}：域名可注册", domain)
                    if not settings["auto_register"] or not api_configs:
                        return "available"
                    return self._register(domain, api_configs)
                self._log("info", "availability", f"{availability_api['name']}：域名不可注册，继续查询 WHOIS", domain)
            except Exception as exc:
                self._save_domain_state(domain, "monitoring", str(exc))
                self._log("warning", "availability", f"可注册查询失败，将在下一轮重试：{exc}", domain, str(exc))
                return "error"

        self._log("info", "whois", "开始检查注册状态", domain)
        info: dict[str, Any] | None = None
        last_error = ""
        for attempt in range(settings["whois_retries"]):
            try:
                info = lookup(domain)
                break
            except Exception as exc:
                last_error = str(exc)
                self._log("warning", "whois", f"WHOIS 查询失败（第 {attempt + 1} 次）", domain, last_error)
        if info is None:
            self._save_domain_state(domain, "monitoring", last_error)
            return "error"

        checked_at = now()
        if is_available(info):
            if availability_api:
                reason = "注册商 API 显示不可注册，WHOIS 未发现明确的在册信息"
                self._save_domain_state(domain, "monitoring", reason)
                self._log("warning", "whois", reason, domain)
                return "monitoring"
            self._save_domain_state(domain, "available", "")
            self._log("info", "whois", "域名可注册", domain)
            if not settings["auto_register"] or not api_configs:
                return "available"
            return self._register(domain, api_configs)

        status = deletion_status(info)
        if status == "未过期" or (info.get("statuses") and status not in {"已过期", "赎回期", "待删除"}):
            reason = "域名已续费或已被注册"
            self._kick(domain, reason, json.dumps(info, ensure_ascii=False))
            self._save_domain_state(domain, "kicked", reason)
            self._log("warning", "kick", reason, domain)
            return "kicked"

        self._save_domain_state(domain, "monitoring", "")
        self._log("info", "whois", f"当前状态：{status or '待继续观察'}", domain)
        return "monitoring"

    def _register(self, domain: str, api_configs: list[dict[str, Any]]) -> str:
        success = False
        with ThreadPoolExecutor(max_workers=len(api_configs), thread_name_prefix="domain-register") as pool:
            futures = {pool.submit(self._register_one, domain, config): config for config in api_configs}
            for future in as_completed(futures):
                config = futures[future]
                try:
                    if future.result():
                        success = True
                except Exception as exc:
                    self._log("error", "register", f"注册商调用异常：{exc}", domain, config["name"])
        if success:
            self._save_domain_state(domain, "registered", "")
            self._log("info", "register", "至少一个注册商已提交抢注", domain)
            return "registered"
        self._save_domain_state(domain, "available", "所有注册商均未注册成功")
        return "available"

    def _register_one(self, domain: str, config: dict[str, Any]) -> bool:
        started = now()
        try:
            adapter = create_adapter(config, decrypt_cookie(config.get("token_ciphertext", "")))
            result = adapter.register(domain)
            db.execute(
                "INSERT INTO registration_attempts (domain, registrar_api_id, status, response, attempted_at, completed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (domain, config["id"], "success" if result.success else "failure", result.response, started, now()),
            )
            if result.success:
                self._record_registered(domain, config, result.response)
            self._log(
                "info" if result.success else "warning",
                "register",
                f"{config['name']}：{'提交成功' if result.success else '提交失败'}（HTTP {result.status_code}）",
                domain,
                result.response,
            )
            return result.success
        except Exception as exc:
            db.execute(
                "INSERT INTO registration_attempts (domain, registrar_api_id, status, error, attempted_at, completed_at) VALUES (?, ?, 'error', ?, ?, ?)",
                (domain, config["id"], str(exc), started, now()),
            )
            self._log("error", "register", f"{config['name']}：调用失败", domain, str(exc))
            return False

    def _selected_apis(self, api_ids: list[int]) -> list[dict[str, Any]]:
        if not api_ids:
            return []
        placeholders = ",".join("?" for _ in api_ids)
        return db.fetch_all(
            f"SELECT * FROM registrar_apis WHERE enabled = 1 AND id IN ({placeholders}) ORDER BY id",
            tuple(api_ids),
        )

    @staticmethod
    def _selected_api(api_id: int) -> dict[str, Any] | None:
        if not api_id:
            return None
        return db.fetch_one("SELECT * FROM registrar_apis WHERE enabled = 1 AND id = ?", (api_id,))

    @staticmethod
    def _save_domain_state(domain: str, status: str, last_error: str) -> None:
        db.execute(
            """
            INSERT INTO monitor_domain_state (domain, status, last_checked_at, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                status = excluded.status, last_checked_at = excluded.last_checked_at,
                last_error = excluded.last_error, updated_at = excluded.updated_at
            """,
            (domain, status, now(), last_error, now()),
        )

    @staticmethod
    def _check_snapshot(domain: str) -> dict[str, Any]:
        return db.fetch_one(
            """
            SELECT deletion_status, creation_date, expiration_date, wechat_status,
                   qq_status, pollution_status, blocked_status, blacklist_status,
                   filing_nature, filing_info
            FROM domain_checks WHERE domain = ?
            """,
            (domain,),
        ) or {}

    @classmethod
    def _record_registered(cls, domain: str, config: dict[str, Any], response: str) -> None:
        check = cls._check_snapshot(domain)
        db.execute(
            """
            INSERT OR REPLACE INTO registered_domains (
                domain, registrar_api_id, registrar_name, response, registered_at,
                deletion_status, creation_date, expiration_date, wechat_status, qq_status,
                pollution_status, blocked_status, blacklist_status, filing_nature, filing_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain,
                config["id"],
                config["name"],
                (response or "")[:4000],
                now(),
                check.get("deletion_status", ""),
                check.get("creation_date", ""),
                check.get("expiration_date", ""),
                check.get("wechat_status", "否"),
                check.get("qq_status", "否"),
                check.get("pollution_status", "否"),
                check.get("blocked_status", "否"),
                check.get("blacklist_status", "否"),
                check.get("filing_nature", ""),
                check.get("filing_info", ""),
            ),
        )

    @classmethod
    def _kick(cls, domain: str, reason: str, detail: str) -> None:
        check = cls._check_snapshot(domain)
        db.execute(
            """
            INSERT OR IGNORE INTO kicked_domains (
                domain, reason, detail, kicked_at, deletion_status, creation_date,
                expiration_date, wechat_status, qq_status, pollution_status,
                blocked_status, blacklist_status, filing_nature, filing_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain,
                reason,
                detail[:4000],
                now(),
                check.get("deletion_status", ""),
                check.get("creation_date", ""),
                check.get("expiration_date", ""),
                check.get("wechat_status", "否"),
                check.get("qq_status", "否"),
                check.get("pollution_status", "否"),
                check.get("blocked_status", "否"),
                check.get("blacklist_status", "否"),
                check.get("filing_nature", ""),
                check.get("filing_info", ""),
            ),
        )
        db.execute(
            "UPDATE domain_checks SET result = 'kicked', reason = ? WHERE domain = ? AND result = 'qualified'",
            (reason, domain),
        )

    @staticmethod
    def _log(level: str, stage: str, message: str, domain: str = "", detail: str = "") -> None:
        db.execute(
            "INSERT INTO monitor_logs (created_at, level, stage, domain, message, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (now(), level, stage, domain, message, detail[:4000]),
        )
        db.execute(
            "DELETE FROM monitor_logs WHERE id NOT IN (SELECT id FROM monitor_logs ORDER BY id DESC LIMIT 500)"
        )


monitor_manager = MonitorManager()
