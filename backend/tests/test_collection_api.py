import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, main


class CollectionApiTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        connection.create_function("label_kind_of", 1, db.label_kind)
        database = patch.object(db, "get_connection", return_value=connection)
        database.start()
        self.addCleanup(database.stop)
        authentication = patch.object(main.system_manage, "current_user", return_value={"id": 1})
        authentication.start()
        self.addCleanup(authentication.stop)
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, adapter, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'gname-test', 'Gname', 'gname', '', '', '', '')"
        )
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_manual_collection_endpoints_forward_multiple_suffixes(self):
        suffixes = ["com", "com.cn"]
        with patch.object(main, "queue_date", return_value=1) as queued:
            response = self.client.post("/api/collect", json={
                "source_id": 1, "requested_date": "2026-10-01", "suffixes": suffixes,
            })
            self.assertEqual(response.status_code, 200, response.text)
            queued.assert_called_once_with(1, "2026-10-01", "txt", "manual", suffixes=suffixes)
        with patch.object(main, "queue_latest", return_value=2) as queued:
            response = self.client.post("/api/collect/latest", params={"source_id": 1, "suffixes": suffixes})
            self.assertEqual(response.status_code, 200, response.text)
            queued.assert_called_once_with(1, "txt", "manual-latest", suffixes=suffixes)
        with patch.object(main, "queue_file", return_value=3) as queued:
            response = self.client.post(
                "/api/collect/upload",
                params={"source_id": 1, "requested_date": "2026-10-01", "suffixes": suffixes},
                files={"file": ("domains.txt", b"first.com\nsecond.net", "text/plain")},
            )
            self.assertEqual(response.status_code, 200, response.text)
            queued.assert_called_once_with(
                1, "2026-10-01", b"first.com\nsecond.net", "domains.txt", suffixes=suffixes,
            )

    def test_schedule_persists_normalized_suffixes_and_run_forwards_them(self):
        payload = {"source_id": 1, "name": "Gname", "run_time": "23:59", "suffixes": [" .COM ", "net", "com"]}
        response = self.client.post("/api/schedules", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        schedule = response.json()["data"]
        self.assertEqual(schedule["suffixes"], ["com", "net"])
        with patch.object(main, "queue_latest", return_value=4) as queued:
            response = self.client.post(f"/api/schedules/{schedule['id']}/run")
            self.assertEqual(response.status_code, 200, response.text)
            queued.assert_called_once_with(1, "txt", "manual-schedule", suffixes=["com", "net"])
        payload["suffixes"] = [".ORG"]
        response = self.client.put(f"/api/schedules/{schedule['id']}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        stored = db.fetch_one("SELECT suffixes_json FROM schedules WHERE id = ?", (schedule["id"],))
        self.assertEqual(json.loads(stored["suffixes_json"]), ["org"])

    def test_invalid_filters_are_rejected_before_run_or_schedule_creation(self):
        for suffixes in (["."], ["bad/suffix"], ["com"] * 101):
            with self.subTest(suffixes=suffixes):
                self.assertEqual(self.client.post("/api/collect", json={
                    "source_id": 1, "requested_date": "2026-10-01", "suffixes": suffixes,
                }).status_code, 422)
                self.assertEqual(self.client.post("/api/collect/latest", params={
                    "source_id": 1, "suffixes": suffixes,
                }).status_code, 422)
                self.assertEqual(self.client.post("/api/collect/upload", params={
                    "source_id": 1, "requested_date": "2026-10-01", "suffixes": suffixes,
                }, files={"file": ("domains.txt", b"first.com", "text/plain")}).status_code, 422)
                self.assertEqual(self.client.post("/api/schedules", json={
                    "source_id": 1, "name": "Gname", "run_time": "23:59", "suffixes": suffixes,
                }).status_code, 422)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS count FROM import_runs")["count"], 0)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS count FROM schedules")["count"], 0)


if __name__ == "__main__":
    unittest.main()
