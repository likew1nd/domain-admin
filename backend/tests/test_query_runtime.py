import asyncio
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys_path = str(Path(__file__).resolve().parents[1])
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from app.apihz_client import ApiHzError
from app.boce_client import BoceFatalError
from app import db
from app.query_service import QueryStopped, QueryTaskManager, _is_terminal_icp_error
from app.whois_client import RdapError


class QueryRuntimeTests(unittest.TestCase):
    def test_random_candidates_sample_all_matching_pending_domains(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE domains (domain TEXT PRIMARY KEY, joined_at TEXT, query_time TEXT, suffix TEXT)"
        )
        connection.executemany(
            "INSERT INTO domains VALUES (?, ?, ?, ?)",
            [
                ("a.com", "2026-10-08", "", "com"),
                ("b.com", "2026-10-08", None, "com"),
                ("c.com", "2026-10-07", "", "com"),
                ("d.com", "2026-10-06", "", "com"),
                ("checked.com", "2026-10-09", "2026-10-09", "com"),
                ("excluded.net", "2026-10-09", "", "net"),
            ],
        )
        # 固定随机值使最后两条候选优先，避免概率测试，也能识别先截取再打乱的实现。
        random_values = iter([-1, -2, -3, -4])
        connection.create_function("random", 0, lambda: next(random_values))
        manager = object.__new__(QueryTaskManager)
        filters = {"suffixes": ["com"]}
        with patch.object(db, "get_connection", return_value=connection):
            ordered = manager._candidates(filters, 2)
            self.assertEqual([row["domain"] for row in ordered], ["a.com", "b.com"])
            selected = manager._candidates(filters, 2, random_query=True)
            self.assertEqual([row["domain"] for row in selected], ["d.com", "c.com"])
            connection.executemany(
                "UPDATE domains SET query_time = '2026-10-08' WHERE domain = ?",
                [(row["domain"],) for row in selected],
            )
            connection.execute("INSERT INTO domains VALUES ('new.com', '2026-10-09', '', 'com')")
            connection.create_function("random", 0, lambda: 0)
            remaining = manager._candidates(filters, 20, random_query=True)
            self.assertEqual({row["domain"] for row in remaining}, {"a.com", "b.com", "new.com"})

    def test_random_query_settings_task_storage_and_restart(self):
        from app.main import QueryTaskPayload, create_query_task, get_query_settings, save_query_settings

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        connection.create_function("label_kind_of", 1, db.label_kind)
        manager = QueryTaskManager()
        self.addCleanup(manager._executor.shutdown, wait=False, cancel_futures=True)
        with (
            patch.object(db, "get_connection", return_value=connection),
            patch("app.main.query_manager", manager),
            patch("app.main.apihz_client.has_api_key", return_value=False),
            patch.object(manager._executor, "submit") as submit,
        ):
            db.init_db()
            self.assertFalse(QueryTaskPayload().random_query)
            self.assertFalse(get_query_settings()["data"]["settings"]["random_query"])
            old_task = create_query_task(QueryTaskPayload(continuous=False))["data"]
            self.assertEqual(old_task["random_query"], 0)
            db.execute("UPDATE query_tasks SET status = 'completed' WHERE id = ?", (old_task["id"],))
            # 模拟升级前的数据库与配置：旧任务仍采用顺序查询。
            db.execute("ALTER TABLE query_tasks DROP COLUMN random_query")
            db.init_db()
            self.assertEqual(db.fetch_one("SELECT random_query FROM query_tasks")["random_query"], 0)
            db.execute("INSERT INTO query_settings VALUES (1, '{}', '2026-10-08')")
            self.assertFalse(get_query_settings()["data"]["settings"]["random_query"])

            payload = QueryTaskPayload(random_query=True, continuous=False)
            save_query_settings(payload)
            self.assertTrue(get_query_settings()["data"]["settings"]["random_query"])
            task = create_query_task(payload)["data"]
            self.assertEqual(task["random_query"], 1)
            manager._runtimes.clear()
            submit.reset_mock()
            manager.resume_all()
            self.assertEqual(submit.call_args.args[1], task["id"])
            with (
                patch.object(manager, "_candidates", return_value=[]) as candidates,
                patch.object(manager, "_count_candidates", return_value=1),
            ):
                asyncio.run(manager._run(task["id"], threading.Event()))
            self.assertTrue(candidates.call_args.args[2])
            self.assertEqual(db.fetch_one("SELECT status FROM query_tasks WHERE id = ?", (task["id"],))["status"], "completed")

    def test_random_buffer_reuses_samples_and_skips_domains_completed_during_scan(self):
        manager = QueryTaskManager()
        self.addCleanup(manager._executor.shutdown, wait=False, cancel_futures=True)
        domains = ["busy.com", *[f"domain{index}.com" for index in range(24)], "new.com"]
        queried = []
        release_busy = asyncio.Event()
        busy_finished = asyncio.Event()
        task = {
            "id": 1, "threads": 2, "filters_json": "{}", "proxy_json": "{}", "total_count": 0,
            "whois_retries": 1, "continuous": False, "random_query": True,
        }

        async def lookup(domain, *args):
            if domain == "busy.com":
                await release_busy.wait()
            await asyncio.sleep(0)
            return {"available": True}

        def save_result(task_id, domain, *args):
            queried.append(domain)
            if domain == "busy.com":
                busy_finished.set()

        batches = iter([
            [{"domain": domain} for domain in domains[:-1]],
            [{"domain": "busy.com"}, {"domain": "new.com"}],
        ])

        def candidates(filters, limit, random_query):
            self.assertEqual(limit, 1000)
            self.assertTrue(random_query)
            return next(batches)

        async def run_thread(func, *args):
            rows = func(*args)
            if rows[-1]["domain"] == "new.com":
                # 扫描期间旧域名完成并移出 in_flight，仍不能把它重新入队。
                self.assertGreaterEqual(len(queried), 5)
                release_busy.set()
                await busy_finished.wait()
                await asyncio.sleep(0)
            return rows

        with (
            patch.object(db, "fetch_one", side_effect=lambda sql, params=(): task if "SELECT *" in sql else {"processed_count": len(queried)}),
            patch.object(db, "execute"),
            patch.object(manager, "_log"),
            patch.object(manager, "_count_candidates", side_effect=lambda filters: len(domains) - len(queried)),
            patch.object(manager, "_candidates", side_effect=candidates) as sample,
            patch.object(manager, "_lookup_whois", side_effect=lookup),
            patch.object(manager, "_save_result", side_effect=save_result),
            patch("app.query_service.match_filters", return_value=(False, "不符合")),
            patch("app.query_service.asyncio.to_thread", side_effect=run_thread),
        ):
            asyncio.run(asyncio.wait_for(manager._run(1, threading.Event()), timeout=3))
        self.assertEqual(sample.call_count, 2)
        self.assertCountEqual(queried, domains)

    def test_no_bind_ip_is_retried(self):
        self.assertFalse(_is_terminal_icp_error(RuntimeError("702, message='No BindIP'")))
        self.assertFalse(_is_terminal_icp_error(RuntimeError("proxy NoBindIP")))
        self.assertFalse(_is_terminal_icp_error(RuntimeError("连接超时")))

    def test_start_rejects_a_second_created_task(self):
        manager = QueryTaskManager()
        inserted = []

        def fetch_one(sql, params=()):
            if "status IN ('created', 'running')" in sql:
                return {"id": inserted[0]} if inserted else None
            return None

        def execute(sql, params=()):
            if sql.lstrip().startswith("INSERT INTO query_tasks"):
                inserted.append(len(inserted) + 1)
                return inserted[-1]
            return 0

        try:
            with (
                patch("app.query_service.db.fetch_one", side_effect=fetch_one),
                patch("app.query_service.db.execute", side_effect=execute),
                patch.object(manager._executor, "submit"),
            ):
                manager.start({})
                with self.assertRaisesRegex(RuntimeError, "已有查询任务正在运行"):
                    manager.start({})
        finally:
            manager._executor.shutdown(wait=False, cancel_futures=True)

    def test_api_box_failures_use_api_box_log_stage_and_message(self):
        with (
            patch(
                "app.query_service.apihz_client.lookup",
                side_effect=ApiHzError("接口盒子返回信息不足，无法确定域名状态"),
            ),
            patch("app.query_service.apihz_client.get_api_key", return_value="key"),
            patch.object(QueryTaskManager, "_log") as log,
        ):
            with self.assertRaisesRegex(ApiHzError, "接口盒子返回信息不足"):
                asyncio.run(
                    QueryTaskManager._lookup_whois(
                        "example.com",
                        1,
                        task_id=7,
                        source="apihz",
                        apihz_id="123",
                    )
                )

        self.assertTrue(log.call_args_list)
        failure = log.call_args_list[-1].args
        self.assertEqual(failure[1], "接口盒子")
        self.assertIn("接口盒子返回信息不足", failure[5])

    def test_whois_timeout_waits_for_previous_thread_before_retrying(self):
        active = 0
        peak = 0
        calls = 0
        lock = threading.Lock()

        def slow_lookup(domain, proxy=None):
            nonlocal active, peak, calls
            with lock:
                calls += 1
                call_number = calls
                active += 1
                peak = max(peak, active)
            time.sleep(0.15 if call_number == 1 else 0.001)
            with lock:
                active -= 1
            return {"expiration_date": "2020-01-01", "statuses": ["pendingDelete"]}

        with patch("app.query_service.lookup", side_effect=slow_lookup):
            with patch("app.query_service.WHOIS_TIMEOUT_SECONDS", 0.05):
                result = asyncio.run(QueryTaskManager._lookup_whois("example.com", 2))

        self.assertEqual(result["statuses"], ["pendingDelete"])
        self.assertEqual(calls, 2)
        self.assertEqual(peak, 1)

    def test_stop_after_current_whois_attempt_skips_remaining_retries(self):
        stop_event = threading.Event()
        calls = 0

        def failed_lookup(domain, proxy=None):
            nonlocal calls
            calls += 1
            stop_event.set()
            raise RuntimeError("temporary failure")

        with patch("app.query_service.lookup", side_effect=failed_lookup):
            with self.assertRaises(QueryStopped):
                asyncio.run(
                    QueryTaskManager._lookup_whois(
                        "example.com", 99, stop_event=stop_event
                    )
                )

        self.assertEqual(calls, 1)

    def test_fatal_intercept_error_releases_proxy_slot(self):
        class Pool:
            def __init__(self):
                self.reports = []

            async def acquire(self):
                return "proxy:1"

            async def report(self, proxy, success):
                self.reports.append((proxy, success))

        class Client:
            async def check(self, item, domain, proxy):
                raise BoceFatalError("波点不足")

        pool = Pool()
        statuses = {"qq_status": "未检测"}
        manager = object.__new__(QueryTaskManager)
        with patch.object(QueryTaskManager, "_log"):
            with self.assertRaises(BoceFatalError):
                asyncio.run(
                    manager._intercept_check(
                        {"id": 1, "qq_retries": 1},
                        "example.com",
                        ["qq"],
                        Client(),
                        pool,
                        {"qq"},
                        statuses,
                    )
                )

        self.assertEqual(pool.reports, [("proxy:1", False)])

    def test_whois_fallback_is_direct_even_when_rdap_has_a_proxy(self):
        whois_result = type(
            "WhoisResult",
            (),
            {
                "expiration_date": "2020-01-01",
                "creation_date": "2010-01-01",
                "status": "pendingDelete",
                "registrar": "",
                "name_servers": [],
                "raw": "raw",
            },
        )()
        with (
            patch("app.whois_client._lookup_rdap", side_effect=RdapError("RDAP unavailable")),
            patch("app.whois_client.whois.whois", return_value=whois_result) as whois_lookup,
        ):
            from app.whois_client import lookup

            lookup("example.com", "http://127.0.0.1:8080")

        whois_lookup.assert_called_once_with("example.com", inc_raw=True)


if __name__ == "__main__":
    unittest.main()
