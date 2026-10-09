import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dashboard, db, main
from app.monitor_service import MonitorManager
from app.query_service import QueryTaskManager
from app.registrar_adapters import RegisterResult


class DomainQualificationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        self.enterContext(patch.object(db, "get_connection", return_value=self.connection))
        self.enterContext(patch.object(main.system_manage, "current_user", return_value={"id": 1}))
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, adapter, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'test', '测试来源', 'generic', '', '', '', '')"
        )
        db.upsert_domains(
            {"id": 1, "name": "测试来源"}, "2026-10-09",
            {"new.com", "rejected.com", "kicked.com", "registered.com", "keep.com"},
        )
        db.execute(
            "INSERT INTO query_tasks (id, name, filters_json, proxy_json, status, created_at, updated_at) "
            "VALUES (1, '查询任务', '{}', '{}', 'completed', '', '')"
        )
        db.execute(
            "INSERT INTO domain_checks (domain, task_id, result, filing_info, checked_at) "
            "VALUES ('rejected.com', 1, 'unqualified', '保留备案信息', '2026-10-08')"
        )
        db.execute(
            "INSERT INTO monitor_domain_state (domain, status, last_error, updated_at) "
            "VALUES ('kicked.com', 'kicked', '旧错误', ''), ('registered.com', 'registered', '', '')"
        )
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.addCleanup(dashboard.invalidate_stats)

    def qualify(self, domains):
        return self.client.post("/api/domains/qualify-selected", json={"domains": domains})

    def test_selected_domains_enter_list_and_monitor_without_losing_query_details(self):
        selected = [" NEW.COM ", "new.com", "rejected.com", "kicked.com", "registered.com"]
        selected.extend(f"missing{i}.com" for i in range(600))
        # 正在运行的监控允许加入，下轮监控应读取到新域名。
        db.execute("UPDATE monitor_state SET status = 'running' WHERE id = 1")
        response = self.qualify(selected)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {"added": 3, "skipped": 601})
        listing = self.client.get("/api/query-results", params={"result": "qualified"}).json()["data"]
        self.assertEqual({row["domain"] for row in listing["records"]}, {"new.com", "rejected.com", "kicked.com"})
        rejected = db.fetch_one("SELECT * FROM domain_checks WHERE domain = 'rejected.com'")
        self.assertEqual(rejected["filing_info"], "保留备案信息")
        self.assertEqual(rejected["checked_at"], "2026-10-08")
        self.assertEqual(db.fetch_one("SELECT query_time FROM domains WHERE domain = 'rejected.com'")["query_time"], "2026-10-08")
        self.assertEqual(rejected["reason"], "手动加入符合列表")
        self.assertEqual(self.client.get("/api/query-results?result=unqualified").json()["data"]["total"], 0)
        pending = QueryTaskManager()._candidates({}, 20)
        self.assertEqual({row["domain"] for row in pending}, {"keep.com", "registered.com"})

        manager = MonitorManager()
        with patch.object(manager, "_selected_apis", return_value=[]), \
             patch.object(manager, "_selected_api", return_value={"id": 1}), \
             patch.object(manager, "_check_domain", return_value="monitoring") as check:
            manager._run_cycle({"api_ids": [], "availability_api_id": 1, "concurrency": 1}, threading.Event())
        self.assertEqual({call.args[0] for call in check.call_args_list}, {"new.com", "rejected.com", "kicked.com"})
        self.assertEqual(db.fetch_one("SELECT last_error FROM monitor_domain_state WHERE domain = 'kicked.com'")["last_error"], "")
        self.assertEqual(self.qualify(selected).json()["data"], {"added": 0, "skipped": 604})
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS count FROM query_tasks")["count"], 2)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_invalid_selection_and_active_query_do_not_modify_data(self):
        for domains in ([], ["  "]):
            self.assertEqual(self.qualify(domains).status_code, 422)
        for status in ("created", "running"):
            db.execute("UPDATE query_tasks SET status = ? WHERE id = 1", (status,))
            self.assertEqual(self.qualify(["new.com"]).status_code, 409)
        self.assertIsNone(db.fetch_one("SELECT * FROM domain_checks WHERE domain = 'new.com'"))
        db.execute("UPDATE query_tasks SET status = 'completed' WHERE id = 1")
        with patch.object(main.system_manage, "current_user", side_effect=main.system_manage.ApiError("请先登录", "8888")):
            self.assertEqual(self.qualify(["new.com"]).json()["code"], "8888")
        self.assertIsNone(db.fetch_one("SELECT * FROM domain_checks WHERE domain = 'new.com'"))

    def test_registered_domain_leaves_qualified_list_and_stats_but_keeps_history(self):
        self.qualify(["new.com", "keep.com"])
        db.execute("INSERT INTO registrar_apis (id, name, endpoint, created_at, updated_at) VALUES (8, '123', '', '', '')")
        manager = MonitorManager()
        with patch("app.monitor_service.create_adapter") as create, \
             patch("app.monitor_service.decrypt_cookie", return_value=""):
            create.return_value.register.return_value = RegisterResult(True, 200, '{"code":200}')
            self.assertTrue(manager._register_one("new.com", {"id": 8, "name": "123"}))
        manager._save_domain_state("new.com", "registered", "")

        for missing_table in (None, "registered_domains", "monitor_domain_state"):
            with self.subTest(missing_table=missing_table):
                manager._record_registered("new.com", {"id": 8, "name": "123"}, '{"code":200}')
                manager._save_domain_state("new.com", "registered", "")
                if missing_table:
                    db.execute(f"DELETE FROM {missing_table} WHERE domain = 'new.com'")
                listing = self.client.get("/api/query-results", params={"result": "qualified", "with_stats": True}).json()["data"]
                self.assertEqual([row["domain"] for row in listing["records"]], ["keep.com"])
                self.assertEqual(listing["total"], 1)
                self.assertEqual(sum(row["count"] for row in listing["stats"]["source"]), 1)
                with patch.object(manager, "_selected_apis", return_value=[]), \
                     patch.object(manager, "_selected_api", return_value={"id": 8}), \
                     patch.object(manager, "_check_domain", return_value="monitoring") as check:
                    manager._run_cycle({"api_ids": [], "availability_api_id": 8, "concurrency": 1}, threading.Event())
                self.assertEqual([call.args[0] for call in check.call_args_list], ["keep.com"])
        manager._record_registered("new.com", {"id": 8, "name": "123"}, '{"code":200}')
        history = self.client.get("/api/registered-domains").json()["data"]
        self.assertEqual([row["domain"] for row in history["records"]], ["new.com"])
        self.assertIsNotNone(db.fetch_one("SELECT * FROM domain_checks WHERE domain = 'new.com'"))

    def test_failure_rolls_back_whole_addition(self):
        self.connection.execute(
            "CREATE TRIGGER reject_monitor_insert BEFORE INSERT ON monitor_domain_state "
            "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.qualify(["new.com"])
        self.assertIsNone(db.fetch_one("SELECT * FROM domain_checks WHERE domain = 'new.com'"))
        self.assertEqual(db.fetch_one("SELECT query_time FROM domains WHERE domain = 'new.com'")["query_time"], "")
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS count FROM query_tasks")["count"], 1)

    def test_batch_kick_moves_only_selected_qualified_domains_and_preserves_snapshot(self):
        self.qualify(["new.com", "keep.com", "kicked.com"])
        db.execute("UPDATE domain_checks SET filing_info = '备案快照' WHERE domain = 'new.com'")
        db.execute("INSERT INTO registered_domains (domain, registrar_api_id, registered_at) VALUES ('kicked.com', 8, '')")
        selected = [" NEW.COM ", "new.com", "rejected.com", "registered.com", "kicked.com"]
        selected.extend(f"missing{i}.com" for i in range(600))
        response = self.client.post("/api/query-results/kick-selected", json={"domains": selected})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], {"kicked": 1, "skipped": 603})
        listing = self.client.get("/api/query-results?result=qualified&with_stats=true").json()["data"]
        self.assertEqual([row["domain"] for row in listing["records"]], ["keep.com"])
        self.assertEqual(listing["total"], 1)
        kicked = self.client.get("/api/kicked-domains").json()["data"]["records"]
        self.assertEqual([row["domain"] for row in kicked], ["new.com"])
        self.assertEqual(kicked[0]["filing_info"], "备案快照")
        self.assertEqual(kicked[0]["reason"], "手动批量踢出")
        self.assertEqual(db.fetch_one("SELECT status FROM monitor_domain_state WHERE domain = 'new.com'")["status"], "kicked")
        self.assertIsNotNone(db.fetch_one("SELECT * FROM domains WHERE domain = 'new.com'"))
        self.assertEqual(self.client.post("/api/query-results/kick-selected", json={"domains": ["new.com"]}).json()["data"], {"kicked": 0, "skipped": 1})
        self.assertEqual(self.qualify(["new.com"]).json()["data"]["added"], 1)
        self.assertEqual(self.client.post("/api/query-results/kick-selected", json={"domains": ["new.com"]}).json()["data"]["kicked"], 1)

    def test_batch_kick_requires_valid_selection_login_and_stopped_tasks(self):
        self.qualify(["new.com"])
        for domains in ([], ["  "]):
            self.assertEqual(self.client.post("/api/query-results/kick-selected", json={"domains": domains}).status_code, 422)
        for table, statuses in (("query_tasks", ("created", "running")), ("monitor_state", ("running", "stopping"))):
            for status in statuses:
                db.execute(f"UPDATE {table} SET status = ? WHERE id = 1", (status,))
                self.assertEqual(self.client.post("/api/query-results/kick-selected", json={"domains": ["new.com"]}).status_code, 409)
                db.execute(f"UPDATE {table} SET status = 'stopped' WHERE id = 1")
        with patch.object(main.system_manage, "current_user", side_effect=main.system_manage.ApiError("请先登录", "8888")):
            self.assertEqual(self.client.post("/api/query-results/kick-selected", json={"domains": ["new.com"]}).json()["code"], "8888")
        self.assertIsNone(db.fetch_one("SELECT * FROM kicked_domains WHERE domain = 'new.com'"))

    def test_batch_kick_failure_rolls_back_snapshots_and_states(self):
        self.qualify(["new.com", "keep.com"])
        self.connection.execute(
            "CREATE TRIGGER reject_kick BEFORE UPDATE ON monitor_domain_state "
            "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post("/api/query-results/kick-selected", json={"domains": ["new.com", "keep.com"]})
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS count FROM kicked_domains")["count"], 0)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS count FROM domain_checks WHERE result = 'qualified'")["count"], 2)
        self.assertEqual(db.fetch_one("SELECT status FROM monitor_domain_state WHERE domain = 'new.com'")["status"], "monitoring")


if __name__ == "__main__":
    unittest.main()
