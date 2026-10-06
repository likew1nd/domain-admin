import asyncio
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
from app.query_service import QueryStopped, QueryTaskManager, _is_terminal_icp_error
from app.whois_client import RdapError


class QueryRuntimeTests(unittest.TestCase):
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
