import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dashboard, db, main


class DomainDeletionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        self.connection.create_function("label_kind_of", 1, db.label_kind)
        for target, name, value in (
            (db, "get_connection", self.connection),
            (main.system_manage, "current_user", {"id": 1}),
        ):
            mocked = patch.object(target, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, adapter, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'test', '测试来源', 'generic', '', '', '', '')"
        )
        db.execute(
            "INSERT INTO query_tasks (id, name, filters_json, proxy_json, status, created_at, updated_at) "
            "VALUES (1, '测试任务', '{}', '{}', 'completed', '', '')"
        )
        self.target_domains = {f"target{index:03}.com" for index in range(25)}
        db.upsert_domains(
            {"id": 1, "name": "测试来源"}, "2026-10-08", self.target_domains | {"keep.net", "unchecked.com"}
        )
        with self.connection:
            self.connection.executemany(
                "INSERT INTO domain_checks (domain, task_id, result, deletion_status, filing_nature, checked_at) "
                "VALUES (?, 1, 'qualified', '待删除', '企业', '2026-10-08T02:00:00+00:00')",
                [(domain,) for domain in self.target_domains],
            )
            self.connection.execute(
                "INSERT INTO domain_checks (domain, task_id, result, checked_at) "
                "VALUES ('keep.net', 1, 'unqualified', '2026-10-07')"
            )
            self.connection.executemany(
                "INSERT INTO monitor_domain_state (domain, updated_at) VALUES (?, '')",
                [(domain,) for domain in self.target_domains | {"keep.net"}],
            )
            self.connection.execute(
                "INSERT INTO kicked_domains (domain, reason, kicked_at) VALUES ('target000.com', '历史原因', '')"
            )
            self.connection.execute(
                "INSERT INTO registered_domains (domain, registrar_api_id, registered_at) VALUES ('target000.com', 1, '')"
            )
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.addCleanup(setattr, main, "_suffix_cache", None)
        self.addCleanup(dashboard.invalidate_stats)

    def domains_in(self, table):
        return {row["domain"] for row in db.fetch_all(f"SELECT domain FROM {table}")}

    def test_filtered_delete_matches_paginated_list_and_cleans_related_rows(self):
        filters = {
            "suffix": "com", "deletion_status": "待删除", "filing_nature": "企业",
            "domain_composition": "letter,digit", "length": "9", "source": "测试来源",
            "query_start": "2026-10-08", "query_end": "2026-10-08", "wechat_status": "否",
        }
        listing = self.client.get("/api/domains", params={**filters, "page_size": 20, "with_stats": True})
        self.assertEqual(listing.status_code, 200, listing.text)
        data = listing.json()["data"]
        self.assertEqual(len(data["records"]), 20)
        self.assertEqual(data["total"], 25)
        main._suffix_cache = (0, [])
        dashboard._stats_cache = (0, {})

        response = self.client.post("/api/domains/delete-filtered", json=filters)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["deleted"], data["total"])
        self.assertEqual(self.domains_in("domains"), {"keep.net", "unchecked.com"})
        self.assertEqual(self.domains_in("domain_checks"), {"keep.net"})
        self.assertEqual(self.domains_in("monitor_domain_state"), {"keep.net"})
        self.assertEqual(self.domains_in("registered_domains"), {"target000.com"})
        self.assertEqual(self.domains_in("kicked_domains"), {"target000.com"})
        self.assertIsNone(main._suffix_cache)
        self.assertIsNone(dashboard._stats_cache)

    def test_selected_delete_is_exact_and_handles_duplicates_missing_and_large_batches(self):
        domains = ["target000.com", "target000.com", *[f"missing{index}.com" for index in range(600)]]
        response = self.client.post("/api/domains/delete-selected", json={"domains": domains})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["deleted"], 1)
        self.assertEqual(len(self.domains_in("domains")), 26)
        for table in ("domains", "domain_checks", "monitor_domain_state"):
            self.assertNotIn("target000.com", self.domains_in(table))
            self.assertIn("target001.com", self.domains_in(table))

    def test_clear_deletes_all_list_data_and_keeps_independent_history(self):
        response = self.client.delete("/api/domains")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["deleted"], 27)
        for table in ("domains", "domain_checks", "monitor_domain_state"):
            self.assertEqual(self.domains_in(table), set())
        for table in ("registered_domains", "kicked_domains"):
            self.assertEqual(self.domains_in(table), {"target000.com"})
        self.assertEqual(self.client.delete("/api/domains").json()["data"]["deleted"], 0)

    def test_empty_invalid_and_unrecognized_scopes_cannot_clear_data(self):
        for body in ({}, {"domain": "  "}, {"length": "invalid"}, {"suffix": ","}, {"page": 1}):
            with self.subTest(filters=body):
                response = self.client.post("/api/domains/delete-filtered", json=body)
                self.assertIn(response.status_code, (400, 422), response.text)
        for body in ({}, {"domains": []}, {"domains": ["  "]}):
            with self.subTest(selection=body):
                self.assertEqual(self.client.post("/api/domains/delete-selected", json=body).status_code, 422)
        self.assertEqual(len(self.domains_in("domains")), 27)

    def test_active_query_or_monitor_blocks_every_delete_scope(self):
        for table, statuses in (("query_tasks", ("created", "running")), ("monitor_state", ("running", "stopping"))):
            for status in statuses:
                with self.subTest(table=table, status=status):
                    db.execute(f"UPDATE {table} SET status = ? WHERE id = 1", (status,))
                    responses = [
                        self.client.delete("/api/domains"),
                        self.client.post("/api/domains/delete-selected", json={"domains": ["target000.com"]}),
                        self.client.post("/api/domains/delete-filtered", json={"suffix": "com"}),
                    ]
                    for response in responses:
                        self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(len(self.domains_in("domains")), 27)
                    self.assertEqual(len(self.domains_in("domain_checks")), 26)
                    db.execute(f"UPDATE {table} SET status = 'stopped' WHERE id = 1")

    def test_failed_delete_rolls_back_related_cleanup(self):
        self.connection.execute(
            "CREATE TRIGGER reject_domain_delete BEFORE DELETE ON domains "
            "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
        )
        self.connection.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            main.delete_filtered_domains(main.DomainListFilters(deletion_status="待删除"))
        self.assertEqual(len(self.domains_in("domains")), 27)
        self.assertEqual(len(self.domains_in("domain_checks")), 26)
        self.assertEqual(len(self.domains_in("monitor_domain_state")), 26)
        self.connection.execute("DROP TRIGGER reject_domain_delete")
        self.connection.commit()
        self.assertEqual(main.delete_filtered_domains(main.DomainListFilters(deletion_status="待删除"))["data"]["deleted"], 25)

    def test_delete_endpoints_require_existing_login_middleware(self):
        with patch.object(main.system_manage, "current_user", side_effect=main.system_manage.ApiError("请先登录", "8888")):
            responses = [
                self.client.delete("/api/domains"),
                self.client.post("/api/domains/delete-selected", json={"domains": ["target000.com"]}),
                self.client.post("/api/domains/delete-filtered", json={"suffix": "com"}),
            ]
        self.assertTrue(all(response.json()["code"] == "8888" for response in responses))
        self.assertEqual(len(self.domains_in("domains")), 27)


if __name__ == "__main__":
    unittest.main()
