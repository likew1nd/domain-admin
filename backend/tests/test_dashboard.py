import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dashboard, db


class DashboardRuntimeTests(unittest.TestCase):
    def test_dashboard_overlays_live_domain_counts_on_cached_stats(self):
        with (
            patch.object(dashboard, "_runtime", return_value={"registrarApis": {"enabled": 0}}),
            patch.object(dashboard, "_cached_stats", return_value={"total": 10, "queried": 1, "pending": 9}),
            patch.object(
                dashboard,
                "_live_stats",
                return_value={"total": 10, "queried": 4, "pending": 6, "queryRate": 40.0},
            ),
            patch.object(dashboard, "_check_stats", return_value={}),
            patch.object(dashboard, "_alerts", return_value=[]),
            patch.object(dashboard, "_activities", return_value=[]),
        ):
            response = dashboard.get_dashboard()

        self.assertEqual(response["data"]["stats"]["queried"], 4)
        self.assertEqual(response["data"]["stats"]["pending"], 6)

    def test_runtime_includes_live_domain_counts(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE domains (domain TEXT, query_time TEXT, imported_at TEXT)")
        connection.executemany(
            "INSERT INTO domains VALUES (?, '', ?)",
            [("a.com", db.utc_now()), ("b.com", db.utc_now())],
        )
        with (
            patch.object(dashboard, "_runtime", return_value={"registrarApis": {"enabled": 0}}),
            patch.object(dashboard, "_check_stats", return_value={}),
            patch.object(dashboard, "_alerts", return_value=[]),
            patch.object(dashboard, "_activities", return_value=[]),
            patch.object(dashboard.db, "get_connection", return_value=connection),
        ):
            self.assertEqual(dashboard.get_dashboard_runtime()["data"]["stats"]["queried"], 0)
            connection.execute("UPDATE domains SET query_time = '2026-10-06' WHERE domain = 'a.com'")
            self.assertEqual(
                dashboard.get_dashboard_runtime()["data"]["stats"],
                {
                    "total": 2,
                    "queried": 1,
                    "pending": 1,
                    "queryRate": 50.0,
                    "todayJoined": 2,
                    "yesterdayJoined": 0,
                },
            )
            connection.execute("UPDATE domains SET query_time = ''")
            self.assertEqual(dashboard.get_dashboard_runtime()["data"]["stats"]["queried"], 0)
            connection.execute("DELETE FROM domains")
            self.assertEqual(
                dashboard.get_dashboard_runtime()["data"]["stats"],
                {
                    "total": 0,
                    "queried": 0,
                    "pending": 0,
                    "queryRate": 0.0,
                    "todayJoined": 0,
                    "yesterdayJoined": 0,
                },
            )

    def test_live_new_domain_counts_use_first_import_time_not_latest_collection_date(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE domains (domain TEXT, query_time TEXT, imported_at TEXT, joined_at TEXT)")
        yesterday_start, _ = dashboard._local_day_bounds(date.today() - timedelta(days=1))
        connection.executemany(
            "INSERT INTO domains VALUES (?, '', ?, ?)",
            [
                # This domain was first imported yesterday and appeared again in today's collection.
                ("existing.com", yesterday_start, date.today().isoformat()),
                ("new-one.com", db.utc_now(), date.today().isoformat()),
                ("new-two.com", db.utc_now(), date.today().isoformat()),
            ],
        )
        with patch.object(dashboard.db, "get_connection", return_value=connection):
            stats = dashboard._live_stats()

        self.assertEqual(stats["todayJoined"], 2)
        self.assertEqual(stats["yesterdayJoined"], 1)

    def test_runtime_booking_counts_distinct_domains(self):
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        with patch.object(dashboard.db, "get_connection", return_value=connection):
            db.init_db()
            connection.executemany(
                """
                INSERT INTO booking_attempts (
                    domain, registrar_api_id, registrar_name, channel, scheme_name, status, created_at
                ) VALUES (?, 1, '预订商', ?, '方案', ?, '')
                """,
                [
                    ("booked.com", 1, "failure"),
                    ("booked.com", 2, "success"),
                    ("pending.com", 1, "pending"),
                    ("failed.com", 1, "failure"),
                ],
            )
            booking = dashboard._runtime()["booking"]

        self.assertEqual(booking, {"total": 3, "success": 1, "processing": 1, "uncertain": 0, "failure": 1})


if __name__ == "__main__":
    unittest.main()
