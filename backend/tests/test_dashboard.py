import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dashboard


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
        connection.execute("CREATE TABLE domains (domain TEXT, query_time TEXT)")
        connection.executemany("INSERT INTO domains VALUES (?, '')", [("a.com",), ("b.com",)])
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
                {"total": 2, "queried": 1, "pending": 1, "queryRate": 50.0},
            )
            connection.execute("UPDATE domains SET query_time = ''")
            self.assertEqual(dashboard.get_dashboard_runtime()["data"]["stats"]["queried"], 0)
            connection.execute("DELETE FROM domains")
            self.assertEqual(
                dashboard.get_dashboard_runtime()["data"]["stats"],
                {"total": 0, "queried": 0, "pending": 0, "queryRate": 0.0},
            )


if __name__ == "__main__":
    unittest.main()
