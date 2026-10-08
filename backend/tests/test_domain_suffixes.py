import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, main


class DomainSuffixTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        self.connection.create_function("label_kind_of", 1, db.label_kind)
        for target, name, value in (
            (db, "get_connection", self.connection),
            (main.system_manage, "current_user", {"id": 1}),
        ):
            mocked = patch.object(target, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        main._suffix_cache = None
        self.addCleanup(setattr, main, "_suffix_cache", None)
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'test', '测试来源', '', '', '', '')"
        )
        self.source = {"id": 1, "name": "测试来源"}
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def suffixes(self):
        response = self.client.get("/api/domains/suffixes")
        self.assertEqual(response.status_code, 200, response.text)
        return {row["suffix"]: row for row in response.json()["data"]}

    def test_import_immediately_refreshes_empty_and_populated_suffix_counts(self):
        self.assertEqual(self.suffixes(), {})
        db.upsert_domains(self.source, "2026-10-08", {"first.com"})
        self.assertEqual(self.suffixes(), {"com": {"suffix": "com", "count": 1, "group": "three"}})
        db.upsert_domains(self.source, "2026-10-08", {"second.com", "first.net"})
        self.assertEqual({key: row["count"] for key, row in self.suffixes().items()}, {"com": 2, "net": 1})

    def test_migration_repairs_missing_suffix_with_existing_length(self):
        db.upsert_domains(self.source, "2026-10-08", {"example.com.cn"})
        db.execute("UPDATE domains SET suffix = ''")
        db.init_db()
        self.assertEqual(self.suffixes(), {"cn": {"suffix": "cn", "count": 1, "group": "two"}})

    def test_delete_and_import_refresh_suffixes_even_when_total_count_is_unchanged(self):
        db.upsert_domains(self.source, "2026-10-08", {"first.com"})
        self.assertEqual(set(self.suffixes()), {"com"})
        db.delete_domains("1 = 1", (), "")
        self.assertEqual(self.suffixes(), {})
        db.upsert_domains(self.source, "2026-10-08", {"first.net"})
        self.assertEqual(set(self.suffixes()), {"net"})

    def test_idn_suffix_group_keeps_stored_filter_value(self):
        db.upsert_domains(
            self.source, "2026-10-08", {"example.中国", "example.xn--fiqs8s", "example.рф", "example.xn--p1ai"}
        )
        rows = self.suffixes()
        self.assertEqual(set(rows), {"中国", "xn--fiqs8s", "рф", "xn--p1ai"})
        self.assertEqual(rows["中国"]["group"], "chinese")
        self.assertEqual(rows["xn--fiqs8s"]["group"], "chinese")
        self.assertEqual(rows["рф"]["group"], "two")
        self.assertEqual(rows["xn--p1ai"]["group"], "two")


if __name__ == "__main__":
    unittest.main()
