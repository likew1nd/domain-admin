import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import collector, db
from app.sources.base import DownloadedFile, SourceError, parse_domains
from app.sources.west_cn import WestCnAdapter
from app.sources.aliyun import AliyunAdapter

scheduler_module = importlib.import_module("app.scheduler")


class CollectorSuffixTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        self.connection.create_function("label_kind_of", 1, db.label_kind)
        self.downloads = tempfile.TemporaryDirectory()
        self.addCleanup(self.downloads.cleanup)
        self.content = (
            "Existing.COM\nexisting.com\nfresh.com\nSecond.NET\nfresh.com.cn\n"
            "skip.cn\nbad.notcom\nfalse.notcom.cn\n中文.中国\nidn.xn--fiqs8s\n"
        ).encode()
        self.adapter = Mock()
        self.adapter.available_dates.return_value = ["2026-01-02"]
        self.adapter.download.return_value = DownloadedFile(self.content, "gname.txt", "text/plain")
        for target, name, options in (
            (db, "get_connection", {"return_value": self.connection}),
            (collector, "DOWNLOAD_DIR", {"new": Path(self.downloads.name)}),
            (collector, "create_adapter", {"return_value": self.adapter}),
            (collector.EXECUTOR, "submit", {"side_effect": lambda fn, *args, **kwargs: fn(*args, **kwargs)}),
        ):
            mocked = patch.object(target, name, **options)
            mocked.start()
            self.addCleanup(mocked.stop)
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, adapter, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'gname', 'Gname', 'gname', '', '', '', '')"
        )
        self.source = db.fetch_one("SELECT * FROM sources WHERE id = 1")

    def test_all_manual_entrypoints_filter_before_upsert_and_keep_original_download(self):
        suffixes = [".COM", "net", "com.cn", "com"]
        for mode in ("date", "latest", "file"):
            with self.subTest(mode=mode):
                db.execute("DELETE FROM domains")
                db.upsert_domains(self.source, "2026-01-01", {"existing.com", "skip.cn"})
                if mode == "date":
                    run_id = collector.queue_date(1, "2026-01-02", suffixes=suffixes)
                elif mode == "latest":
                    run_id = collector.queue_latest(1, suffixes=suffixes)
                else:
                    run_id = collector.queue_file(1, "2026-01-02", self.content, "gname.txt", suffixes=suffixes)
                run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
                self.assertEqual(run["status"], "success", run["error"])
                self.assertEqual((run["total_count"], run["inserted_count"], run["updated_count"]), (4, 3, 1))
                rows = db.fetch_all("SELECT domain, joined_at FROM domains")
                self.assertEqual(
                    {row["domain"] for row in rows},
                    {"existing.com", "fresh.com", "second.net", "fresh.com.cn", "skip.cn"},
                )
                self.assertEqual(next(row["joined_at"] for row in rows if row["domain"] == "skip.cn"), "2026-01-01")
                stored_name = f"upload-{run_id}-gname.txt" if mode == "file" else "gname.txt"
                self.assertEqual((Path(self.downloads.name) / "gname" / stored_name).read_bytes(), self.content)

    def test_empty_filter_imports_all_and_unmatched_filter_succeeds_without_rows(self):
        for suffixes, expected_count in (([], len(parse_domains(self.content))), (["org"], 0)):
            with self.subTest(suffixes=suffixes):
                db.execute("DELETE FROM domains")
                run_id = collector.queue_file(1, "2026-01-02", self.content, "gname.txt", suffixes=suffixes)
                run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
                self.assertEqual(run["status"], "success", run["error"])
                self.assertEqual((run["total_count"], run["inserted_count"], run["updated_count"]), (expected_count, expected_count, 0))
                self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM domains")["n"], expected_count)

    def test_unicode_and_punycode_suffixes_match_both_domain_forms(self):
        run_id = collector.queue_file(1, "2026-01-02", self.content, "gname.txt", suffixes=[".中国"])
        run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
        self.assertEqual(run["status"], "success", run["error"])
        self.assertEqual({row["domain"] for row in db.fetch_all("SELECT domain FROM domains")}, {"中文.中国", "idn.xn--fiqs8s"})

    def test_west_download_encoding_is_preserved_through_both_import_paths(self):
        content = "凭证.com\nexample.com\n".encode("gb18030")
        adapter = WestCnAdapter({"base_url": "https://example.com", "source_key": "west_cn"}, "test-cookie")
        self.addCleanup(adapter.close)
        response = httpx.Response(200, content=content, headers={"content-type": "text/plain"})
        with patch.object(adapter.client, "post", return_value=response), patch.object(collector, "create_adapter", return_value=adapter):
            for mode in ("date", "suffix"):
                with self.subTest(mode=mode):
                    db.execute("DELETE FROM domains")
                    run_id = collector._create_run(self.source, "2026-01-02", "txt", "test")
                    if mode == "date":
                        collector._run_date(run_id, self.source, "2026-01-02", "txt", ["com"])
                    else:
                        collector._run_west_suffix(run_id, self.source, "2026-01-02", "com")
                    run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
                    self.assertEqual(run["status"], "success", run["error"])
                    self.assertEqual({row["domain"] for row in db.fetch_all("SELECT domain FROM domains")}, {"凭证.com", "example.com"})

    def test_normalization_validates_before_creating_run(self):
        self.assertEqual(collector.normalize_collection_suffixes([" .COM ", "com", "Com.CN", "中国"]), ["com", "com.cn", "xn--fiqs8s"])
        self.assertEqual(collector.normalize_collection_suffixes(["", " "]), [])
        for suffixes in (["."], ["com/net"], ["%com"], ["a" * 64], ["com"] * 101):
            with self.subTest(suffixes=suffixes), self.assertRaises(SourceError):
                collector.queue_latest(1, suffixes=suffixes)
        self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM import_runs")["n"], 0)

    def test_login_html_fails_without_inserting_script_identifiers(self):
        content = '<!doctype html><html><title>账号登录</title><script>\nwindow.dataLayer\nagl.async\nagl.src\n</script></html>'.encode()
        self.adapter.download.return_value = DownloadedFile(content, "login.txt", "text/plain")
        for mode in ("date", "file"):
            with self.subTest(mode=mode):
                if mode == "date":
                    run_id = collector.queue_date(1, "2026-01-02")
                else:
                    run_id = collector.queue_file(1, "2026-01-02", content, "login.txt")
                run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
                self.assertEqual(run["status"], "failure")
                self.assertIn("Cookie", run["error"])
                self.assertEqual((run["total_count"], run["inserted_count"], run["updated_count"]), (0, 0, 0))
                self.assertEqual(db.fetch_one("SELECT COUNT(*) AS n FROM domains")["n"], 0)

    def test_scheduler_passes_saved_suffixes_to_the_same_import_path(self):
        db.execute(
            "INSERT INTO schedules (source_id, name, run_time, suffixes_json, created_at, updated_at) "
            "VALUES (1, 'Gname', '08:00', ?, '', '')",
            (json.dumps(["net", "com.cn"]),),
        )
        with patch.object(scheduler_module, "datetime") as clock:
            clock.now.return_value = datetime(2026, 1, 2, 9, 0)
            scheduler_module.DailyScheduler()._tick()
            scheduler_module.DailyScheduler()._tick()
        self.assertEqual({row["domain"] for row in db.fetch_all("SELECT domain FROM domains")}, {"second.net", "fresh.com.cn"})
        runs = db.fetch_all("SELECT * FROM import_runs")
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["status"], runs[0]["total_count"]), ("success", 2))

    def test_aliyun_date_latest_and_schedule_pass_suffixes_to_download_and_upsert(self):
        db.execute(
            "UPDATE sources SET adapter = 'aliyun', source_key = 'aliyun', name = '阿里云', "
            "base_url = 'https://domainapi.aliyun.com', download_template = '/partnerReserve/snatchExport.json' WHERE id = 1"
        )
        requests = []

        def respond(request):
            requests.append(request)
            suffix = request.url.params["suffixes"]
            return httpx.Response(200, text=f"fresh.{suffix}\n凭证.{suffix}\n")

        def adapter_factory(source, cookie):
            adapter = AliyunAdapter(source, cookie)
            adapter.client.close()
            adapter.client = httpx.Client(transport=httpx.MockTransport(respond))
            self.addCleanup(adapter.close)
            return adapter

        with patch.object(collector, "create_adapter", side_effect=adapter_factory), patch.object(collector, "decrypt_cookie", return_value="test-cookie"):
            for mode in ("date", "latest", "schedule"):
                with self.subTest(mode=mode):
                    requests.clear()
                    db.execute("DELETE FROM domains")
                    if mode == "date":
                        run_id = collector.queue_date(1, "2026-01-02", suffixes=["com", "net"])
                    elif mode == "latest":
                        run_id = collector.queue_latest(1, suffixes=["com", "net"])
                    else:
                        db.execute(
                            "INSERT INTO schedules (source_id, name, run_time, suffixes_json, created_at, updated_at) "
                            "VALUES (1, '阿里云', '08:00', ?, '', '')", (json.dumps(["com", "net"]),),
                        )
                        with patch.object(scheduler_module, "datetime") as clock:
                            clock.now.return_value = datetime(2026, 1, 2, 9, 0)
                            scheduler_module.DailyScheduler()._tick()
                        run_id = db.fetch_one("SELECT MAX(id) AS id FROM import_runs")["id"]
                    run = db.fetch_one("SELECT * FROM import_runs WHERE id = ?", (run_id,))
                    self.assertEqual(run["status"], "success", run["error"])
                    self.assertEqual((run["total_count"], run["inserted_count"]), (4, 4))
                    self.assertEqual([request.url.params["suffixes"] for request in requests], ["com", "net"])
                    rows = db.fetch_all("SELECT domain, source_name, joined_at FROM domains")
                    self.assertEqual({row["domain"] for row in rows}, {"fresh.com", "fresh.net", "凭证.com", "凭证.net"})
                    self.assertTrue(all(row["source_name"] == "阿里云" and row["joined_at"] == run["requested_date"] for row in rows))


if __name__ == "__main__":
    unittest.main()
