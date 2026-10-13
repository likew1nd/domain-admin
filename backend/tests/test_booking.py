import asyncio
import hashlib
import json
import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlencode

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, main, registrar_adapters, system_manage
from app.booking_service import BookingManager
from app.query_service import QueryTaskManager
from app.registrar_adapters import GnameRegistrar, RegisterResult, RegistrarError


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("reverse", 1, lambda value: str(value)[::-1])
        self.connection.create_function("label_kind_of", 1, db.label_kind)
        self.addCleanup(self.connection.close)
        self.enterContext(patch.object(db, "get_connection", return_value=self.connection))
        db.init_db()
        db.execute("""INSERT INTO sources (id, source_key, name, base_url, download_template, created_at, updated_at)
                      VALUES (1, 'test', 'test', '', '', '', '')""")
        db.upsert_domains({"id": 1, "name": "test"}, "2026-10-13", {"abab.com", "bad.com"})
        db.execute("""INSERT INTO query_tasks (id, name, filters_json, proxy_json, status, created_at, updated_at)
                      VALUES (1, 'test', '{}', '{}', 'completed', '', '')""")
        db.execute("""INSERT INTO domain_checks (domain, task_id, result, creation_date, checked_at)
                      VALUES ('abab.com', 1, 'qualified', '2010-01-01', ''), ('bad.com', 1, 'unqualified', '', '')""")
        self.scheme = {"name": "方案 1", "enabled": True, "suffixes": ["com"],
                       "lengths": [4], "api_id": 1, "channels": [1, 2]}
        self.settings = {"enabled": True, "schemes": [self.scheme, dict(self.scheme, name="重复方案")]}
        self.manager = BookingManager()
        self.manager.save_settings(self.settings)
        self.adapter = Mock()
        self.adapter.backorder_channels.return_value = [
            {"channel": 1, "status": 1, "tlds": ["com"]}, {"channel": 2, "status": 1, "tlds": ["com"]}]
        self.adapter.backorder.return_value = RegisterResult(True, 200, '{"code":1,"data":100}')
        self.enterContext(patch("app.booking_service.gname_account", return_value=({}, self.adapter)))

    def submit_pending(self):
        for _ in range(20):
            attempt = db.fetch_one("SELECT * FROM booking_attempts WHERE status = 'pending' ORDER BY id LIMIT 1")
            if not attempt:
                return
            self.manager._submit(attempt)
        self.fail("预订队列未结束")

    def test_channels_follow_selection_order_and_stop_at_first_success(self):
        self.adapter.backorder_channels.return_value = [
            {"channel": channel, "status": 1, "tlds": ["com"]} for channel in (7, 8, 9)]
        for successes, expected in (
            ({9}, [(9, 'success')]),
            ({8}, [(9, 'failure'), (8, 'success')]),
            ({7}, [(9, 'failure'), (8, 'failure'), (7, 'success')]),
            (set(), [(9, 'failure'), (8, 'failure'), (7, 'failure')]),
        ):
            with self.subTest(successes=successes):
                db.execute("DELETE FROM booking_attempts")
                self.adapter.backorder.reset_mock()
                self.adapter.backorder.side_effect = lambda domain, channel, remark: RegisterResult(
                    channel in successes, 200, '{"code":1}' if channel in successes else '{"code":-1,"msg":"预订失败"}')
                settings = self.manager.save_settings({"enabled": True, "schemes": [dict(self.scheme, channels=[9, 8, 7])]})
                self.manager.scan(settings)
                self.assertEqual([row['channel'] for row in db.fetch_all("SELECT * FROM booking_attempts")], [9])
                self.submit_pending()
                self.manager.scan(settings)
                self.submit_pending()
                self.assertEqual([(row['channel'], row['status']) for row in db.fetch_all("SELECT * FROM booking_attempts ORDER BY id")], expected)
                self.assertEqual([call.args[1] for call in self.adapter.backorder.call_args_list], [channel for channel, _ in expected])

    def test_temporary_booking_failure_waits_and_unsupported_channels_end_in_failure(self):
        settings = self.manager.save_settings(dict(self.settings, interval_seconds=3600))
        self.adapter.backorder.return_value = RegisterResult(False, 200, '{"code":-1,"msg":"暂时无法预订"}')
        self.manager.scan(settings)
        self.submit_pending()
        first = db.fetch_one("SELECT * FROM booking_attempts WHERE channel = 1")
        self.assertEqual(first["status"], "failure")
        self.assertTrue(first["retry_at"])
        self.manager.scan(settings)
        self.assertIsNone(db.fetch_one("SELECT * FROM booking_attempts WHERE status = 'pending'"))

        db.execute("UPDATE booking_attempts SET retry_at = '' WHERE id = ?", (first["id"],))
        self.adapter.backorder.side_effect = [
            RegisterResult(False, 200, '{"code":-1,"msg":"该通道不支持此后缀"}'),
            RegisterResult(False, 200, '{"code":-1,"msg":"该通道不支持此后缀"}')
        ]
        self.manager.scan(settings)
        self.submit_pending()
        self.submit_pending()
        rows = db.fetch_all("SELECT channel, status, retry_at FROM booking_attempts ORDER BY channel")
        self.assertEqual([(row["channel"], row["status"], row["retry_at"]) for row in rows], [(1, "failure", ""), (2, "failure", "")])

    def test_success_or_uncertain_history_cancels_legacy_pending_channels(self):
        for status in ('success', 'uncertain'):
            with self.subTest(status=status):
                db.execute("DELETE FROM booking_attempts")
                for channel in (1, 2):
                    db.execute("""INSERT INTO booking_attempts
                        (domain, registrar_api_id, registrar_name, channel, scheme_name, status, created_at)
                        VALUES ('abab.com', 1, 'Gname', ?, '方案 1', ?, '')""", (channel, status if channel == 1 else 'pending'))
                self.submit_pending()
                self.manager.scan(self.settings)
                self.assertEqual([row['status'] for row in db.fetch_all("SELECT * FROM booking_attempts")], [status])
                self.adapter.backorder.assert_not_called()

    def test_reordering_legacy_pending_channels_uses_current_priority(self):
        self.manager.scan(self.settings)
        stale = db.fetch_one("SELECT * FROM booking_attempts WHERE channel = 1")
        self.manager.save_settings({"enabled": True, "schemes": [dict(self.scheme, channels=[2, 1])]})
        self.manager._submit(stale)
        self.submit_pending()
        self.assertEqual([call.args[1] for call in self.adapter.backorder.call_args_list], [2])
        self.assertEqual([(row['channel'], row['status']) for row in db.fetch_all("SELECT * FROM booking_attempts")], [(2, 'success')])

    def test_multiple_schemes_deduplicate_failed_channels_and_stop_after_success(self):
        settings = self.manager.save_settings({"enabled": True, "schemes": [
            dict(self.scheme, name='首选方案', channels=[1, 2]),
            dict(self.scheme, name='备选方案', channels=[2, 1], api_id=2),
        ]})
        self.adapter.backorder.side_effect = [RegisterResult(False, 200, '{"code":-1}')]*2 + [RegisterResult(True, 200, '{"code":1}')]
        self.manager.scan(settings)
        self.submit_pending()
        self.manager.scan(settings)
        attempts = db.fetch_all("SELECT * FROM booking_attempts ORDER BY id")
        self.assertEqual([(row['registrar_api_id'], row['channel'], row['status'], row['scheme_name']) for row in attempts], [
            (1, 1, 'failure', '首选方案'), (1, 2, 'failure', '首选方案'), (2, 2, 'success', '备选方案')])
        self.assertEqual(self.adapter.backorder.call_count, 3)

    def test_channel_order_validation_preserves_first_selection(self):
        payload = main.BookingSettingsPayload(enabled=False, schemes=[dict(self.scheme, channels=[9, 8, 7, 8])])
        self.assertEqual(payload.schemes[0].channels, [9, 8, 7])
        with patch.object(main, 'booking_manager', self.manager):
            main.save_booking_settings(payload)
            self.assertEqual(main.get_booking_settings()['data']['schemes'][0]['channels'], [9, 8, 7])

    def test_settings_payloads_discard_legacy_match_logic(self):
        legacy = dict(self.scheme, logic="or")
        booking = main.BookingSettingsPayload(enabled=False, schemes=[legacy])
        query = main.QueryTaskPayload(exceptions={"schemes": [legacy]})
        self.assertNotIn("logic", query.model_dump()["exceptions"]["schemes"][0])
        with patch.object(main, "booking_manager", self.manager):
            main.save_booking_settings(booking)
            saved = main.get_booking_settings()["data"]["schemes"][0]
        self.assertNotIn("logic", saved)
        self.assertEqual(saved["lengths"], [4])
        self.assertEqual(saved["suffixes"], ["com"])

    def test_channel_reordered_during_lookup_does_not_submit_stale_priority(self):
        self.manager.scan(self.settings)
        def channels():
            self.manager.save_settings({"enabled": True, "schemes": [dict(self.scheme, channels=[2, 1])]})
            return [{"channel": channel, "status": 1, "tlds": ['com']} for channel in (1, 2)]
        self.adapter.backorder_channels.side_effect = channels
        self.manager._submit(db.fetch_one("SELECT * FROM booking_attempts WHERE status = 'pending'"))
        self.adapter.backorder.assert_not_called()
        self.submit_pending()
        self.assertEqual([call.args[1] for call in self.adapter.backorder.call_args_list], [2])

    def test_matching_dates_nature_and_exclusion(self):
        match = QueryTaskManager._match_exception_scheme
        context = {"creation_date": "2010-01-01T12:00:00Z", "icp_data": {"params": {"list": [
            {"natureName": "企业", "updateRecordTime": "2020-05-01"},
            {"natureName": "个人", "updateRecordTime": "2025-05-01"}]}}}
        scheme = dict(self.scheme, registration_start="2009-01-01", registration_end="2010-01-01",
                      filing_start="2025-01-01", filing_natures=["企业"], patterns=["ABAB"])
        self.assertFalse(match("abab", "com", scheme, context)[0])
        scheme["filing_start"] = "2020-01-01"
        self.assertTrue(match("abab", "com", scheme, context)[0])
        self.assertFalse(match("abab", "com", scheme, {})[0])
        self.assertFalse(match("abab", "com", dict(scheme, logic="or"), {})[0])
        self.assertFalse(match("abab", "com", dict(scheme, logic="or", filing_start="2025-01-01"), context)[0])
        self.assertTrue(match("abab", "com", dict(scheme, logic="or"), context)[0])
        self.assertFalse(match("abab", "com", dict(scheme, logic="or", exclude_chars=["a"]), context)[0])
        self.assertFalse(match("abab", "com", {}, context)[0])
        self.assertFalse(match("aaaa", "com", {"patterns": ["ABAB"]}, context)[0])

    def test_composition_matches_allowed_classes_with_other_conditions(self):
        match = QueryTaskManager._match_exception_scheme
        for classes, accepted, rejected in (
            (["letter"], ["abcd"], ["1234", "ab12", "a-bc", "中文域名", "éabc"]),
            (["digit"], ["1234"], ["abcd", "ab12"]),
            (["letter", "digit"], ["abcd", "1234", "ab12"], ["a-bc", "中文域名"]),
            (["chinese"], ["中文域名"], ["abcd", "中文ab"]),
            (["letter", "symbol"], ["a-bc", "éabc"], ["ab12", "中文ab"]),
        ):
            scheme = dict(self.scheme, domain_composition=classes)
            for label in accepted + rejected:
                with self.subTest(classes=classes, label=label):
                    self.assertEqual(match(label, "com", scheme)[0], label in accepted)
            self.assertFalse(match(accepted[0], "cn", scheme)[0])
        self.assertTrue(match("ab12", "com", dict(self.scheme, domain_composition=[]))[0])

    def test_composition_setting_persists_and_filters_booking_scan(self):
        for domain in ("1234.com", "ab12.com", "a-bc.com"):
            db.execute("INSERT INTO domain_checks (domain, task_id, result, checked_at) VALUES (?, 1, 'qualified', '')", (domain,))
        payload = main.BookingSettingsPayload(enabled=True, schemes=[dict(self.scheme, domain_composition=["letter"])])
        with patch.object(main, "booking_manager", self.manager), patch.object(main, "gname_account", return_value=({}, self.adapter)):
            main.save_booking_settings(payload)
            saved = main.get_booking_settings()["data"]
        self.assertEqual(saved["schemes"][0]["domain_composition"], ["letter"])
        self.manager.scan(saved)
        self.assertEqual({row["domain"] for row in db.fetch_all("SELECT * FROM booking_attempts")}, {"abab.com"})
        self.submit_pending()
        self.assertEqual([call.args[0] for call in self.adapter.backorder.call_args_list], ["abab.com"])
        self.assertEqual(main.BookingSchemePayload(**self.scheme).domain_composition, [])
        with self.assertRaises(ValueError):
            main.BookingSchemePayload(**dict(self.scheme, domain_composition=["unknown"]))

    def test_qualified_only_deduplicates_schemes_and_keeps_monitoring(self):
        self.manager.enqueue("bad.com", self.settings)
        self.manager.enqueue("abab.com", dict(self.settings, enabled=False))
        self.assertEqual(db.fetch_all("SELECT * FROM booking_attempts"), [])
        self.manager.enqueue("abab.com", self.settings)
        self.manager.enqueue("abab.com", self.settings)
        attempts = db.fetch_all("SELECT * FROM booking_attempts")
        self.assertEqual(len(attempts), 1)
        for attempt in attempts:
            self.manager._submit(attempt)
            self.manager._submit(attempt)
        self.assertEqual(self.adapter.backorder.call_count, 1)
        self.assertTrue(all(row["status"] == "success" for row in db.fetch_all("SELECT * FROM booking_attempts")))
        self.assertEqual(db.fetch_all("SELECT * FROM registered_domains"), [])
        self.assertEqual(db.fetch_one("SELECT result FROM domain_checks WHERE domain = 'abab.com'")["result"], "qualified")

    def test_length_and_suffix_required_for_booking_in_monitor_list(self):
        for domain in ("abcd.cn", "longname.com", "longname.cn"):
            db.execute("INSERT INTO domain_checks (domain, task_id, result, checked_at) VALUES (?, 1, 'qualified', '')", (domain,))
        self.adapter.backorder_channels.return_value = [{"channel": 1, "status": 1, "tlds": ["com", "cn"]}]
        expected = {"abab.com"}
        for logic in (None, "or", "and"):
            with self.subTest(logic=logic):
                db.execute("DELETE FROM booking_attempts")
                scheme = dict(self.scheme, channels=[1])
                if logic is not None:
                    scheme["logic"] = logic
                settings = self.manager.save_settings({"enabled": True, "schemes": [scheme]})
                self.manager.scan(settings)
                attempts = db.fetch_all("SELECT * FROM booking_attempts")
                self.assertEqual({row["domain"] for row in attempts}, expected)
                self.adapter.backorder.reset_mock()
                for attempt in attempts:
                    self.manager._submit(attempt)
                self.assertEqual({call.args[0] for call in self.adapter.backorder.call_args_list}, expected)
                self.assertTrue(all(row["status"] == "success" for row in db.fetch_all("SELECT * FROM booking_attempts")))

    def test_query_exception_requires_all_conditions_after_candidate_filters(self):
        db.execute("DELETE FROM domain_checks")
        db.execute("DELETE FROM domains")
        db.upsert_domains({"id": 1, "name": "test"}, "2026-10-13", {"abcd.cn", "longname.com", "abcd.com", "longname.cn"})
        manager = QueryTaskManager()
        self.addCleanup(manager._executor.shutdown, wait=False, cancel_futures=True)
        for logic, lengths, expected in (
            ("or", [], {"abcd.com"}),
            ("and", [], {"abcd.com"}),
            ("or", [4], {"abcd.com"}),
            ("or", [8], set()),
        ):
            with self.subTest(logic=logic, lengths=lengths):
                db.execute("DELETE FROM domain_checks")
                db.execute("UPDATE domains SET query_time = ''")
                payload = main.QueryTaskPayload(continuous=False, proxy_stages=[], delete_type="all", lengths=lengths,
                    exceptions={"schemes": [dict(self.scheme, logic=logic)]})
                with patch.object(main, "query_manager", manager), patch.object(manager._executor, "submit"):
                    task = main.create_query_task(payload)["data"]
                with patch.object(manager, "_lookup_whois", new=AsyncMock(return_value={"available": True})), patch(
                    "app.query_service.IcpClient.query", new=AsyncMock(return_value={"found": False, "queried": True, "data": {}, "records": []})
                ):
                    asyncio.run(manager._run(task["id"], threading.Event()))
                self.assertEqual({row["domain"] for row in db.fetch_all("SELECT domain FROM domain_checks WHERE result = 'qualified'")}, expected)

    def test_query_exception_composition_persists_and_filters_task_results(self):
        db.execute("DELETE FROM domain_checks")
        db.execute("DELETE FROM domains")
        db.upsert_domains({"id": 1, "name": "test"}, "2026-10-13",
                          {"abcd.com", "ab12.com", "1234.com", "a-bc.com", "中文域名.com", "abcd.cn"})
        payload = main.QueryTaskPayload(continuous=False, proxy_stages=[], delete_type="all",
            exceptions={"schemes": [dict(self.scheme, domain_composition=["letter", "digit"], exclude_chars=["3"])]})
        main.save_query_settings(payload)
        saved = main.get_query_settings()["data"]["settings"]
        self.assertEqual(saved["exceptions"]["schemes"][0]["domain_composition"], ["letter", "digit"])
        manager = QueryTaskManager()
        self.addCleanup(manager._executor.shutdown, wait=False, cancel_futures=True)
        with patch.object(main, "query_manager", manager), patch.object(manager._executor, "submit"):
            task = main.create_query_task(main.QueryTaskPayload(**saved))["data"]
        with patch.object(manager, "_lookup_whois", new=AsyncMock(return_value={"available": True})), patch(
            "app.query_service.IcpClient.query", new=AsyncMock(return_value={"found": False, "queried": True, "data": {}, "records": []})
        ):
            asyncio.run(manager._run(task["id"], threading.Event()))
        self.assertEqual({row["domain"] for row in db.fetch_all("SELECT domain FROM domain_checks WHERE result = 'qualified'")},
                         {"abcd.com", "ab12.com"})
        self.assertEqual(main.QueryExceptionSchemePayload().domain_composition, [])
        with self.assertRaises(ValueError):
            main.QueryExceptionSchemePayload(domain_composition=["unknown"])

    def test_timeout_is_uncertain_and_restart_does_not_resubmit(self):
        self.manager.enqueue("abab.com", self.settings)
        attempts = db.fetch_all("SELECT * FROM booking_attempts")
        self.adapter.backorder.side_effect = TimeoutError("timed out")
        self.manager._submit(attempts[0])
        db.execute("INSERT INTO domain_checks (domain, task_id, result, checked_at) VALUES ('test.com', 1, 'qualified', '')")
        self.manager.enqueue('test.com', self.settings)
        db.execute("UPDATE booking_attempts SET status = 'sending' WHERE domain = 'test.com'")
        with patch("app.booking_service.threading.Thread"):
            self.manager.start()
        self.assertEqual([row["status"] for row in db.fetch_all("SELECT * FROM booking_attempts")], ["uncertain", "uncertain"])
        self.manager.enqueue("abab.com", self.settings)
        self.assertEqual(len(db.fetch_all("SELECT * FROM booking_attempts")), 2)
        self.assertEqual(self.adapter.backorder.call_count, 1)

    def test_closed_channel_or_removed_domain_never_submits(self):
        self.manager.enqueue("abab.com", self.settings)
        attempts = db.fetch_all("SELECT * FROM booking_attempts")
        self.adapter.backorder_channels.return_value[0]["status"] = 8
        self.manager._submit(attempts[0])
        next_attempt = db.fetch_one("SELECT * FROM booking_attempts WHERE status = 'pending'")
        self.assertEqual(next_attempt['channel'], 2)
        db.execute("UPDATE domain_checks SET result = 'kicked' WHERE domain = 'abab.com'")
        self.manager._submit(next_attempt)
        self.adapter.backorder.assert_not_called()
        self.assertTrue(all(row["status"] == "failure" for row in db.fetch_all("SELECT * FROM booking_attempts")))

    def test_official_paths_signing_and_business_codes(self):
        adapter = GnameRegistrar({"config": {"extra_params": {"td": 99, "ym": "wrong.com", "appid": "wrong"}}}, "id:key")
        with patch.object(registrar_adapters, "_send", return_value=(200, '{"code":1,"data":100}')) as send:
            self.assertTrue(adapter.backorder("abab.com", 2, "  方案 A~  ").success)
        request = send.call_args.args[0]
        params = {key: value[0] for key, value in parse_qs(request.data.decode()).items()}
        token = params.pop("gntoken")
        self.assertEqual(request.full_url, "https://api.gname.com/api/backorder/add")
        self.assertEqual((params["ym"], params["td"], params["appid"], params["bz"]), ("abab.com", "2", "id", "方案 A~"))
        self.assertEqual(token, hashlib.md5((urlencode(sorted(params.items())).replace('~', '%7E') + "key").encode()).hexdigest().upper())
        with patch.object(adapter, "_request_signed", return_value=(200, '{"code":-1,"msg":"余额不足"}')):
            self.assertFalse(adapter.backorder("abab.com", 1).success)
        with patch.object(adapter, "_request_signed", return_value=(200, 'not json')):
            with self.assertRaises(RegistrarError):
                adapter.backorder("abab.com", 1)
        with patch.object(adapter, "_request_signed", return_value=(200, '{"code":1,"data":[]}')) as request:
            self.assertEqual(adapter.backorder_channels(), [])
            request.assert_called_once_with("/api/backorder/channel", {})

    def test_validation_requires_account_channels_and_conditions(self):
        settings = main.BookingSettingsPayload(enabled=True, schemes=[self.scheme])
        with patch.object(main, "gname_account", return_value=({}, self.adapter)):
            main.validate_booking(settings)
            for changes in ({"channels": []}, {"registration_start": "2026-01-01", "registration_end": "2025-01-01"},
                            {"lengths": [], "suffixes": []}):
                with self.subTest(changes=changes), self.assertRaises(main.HTTPException):
                    main.validate_booking(main.BookingSettingsPayload(enabled=True, schemes=[dict(self.scheme, **changes)]))

    def test_query_pipeline_does_not_book_until_monitor_list_scan(self):
        manager = QueryTaskManager()
        self.addCleanup(manager._executor.shutdown, wait=False, cancel_futures=True)
        payload = main.QueryTaskPayload(continuous=False, proxy_stages=[], delete_type="all")
        with patch.object(main, "gname_account", return_value=({}, self.adapter)), patch.object(main, "query_manager", manager), patch.object(manager._executor, "submit"):
            main.save_query_settings(payload)
            self.assertNotIn("booking", main.get_query_settings()["data"]["settings"])
            task = main.create_query_task(payload)["data"]
        self.assertNotIn("booking", task["filters"])
        async def icp(domain, proxy):
            return {"found": domain == "abab.com", "queried": True, "data": {}, "records": []}
        with patch.object(manager, "_lookup_whois", new=AsyncMock(return_value={"available": True})), patch("app.query_service.IcpClient.query", side_effect=icp), patch("app.booking_service.booking_manager", self.manager):
            asyncio.run(manager._run(task["id"], threading.Event()))
        self.assertEqual(db.fetch_one("SELECT status FROM query_tasks WHERE id = ?", (task["id"],))["status"], "completed")
        self.assertEqual(db.fetch_all("SELECT * FROM booking_attempts"), [])
        self.manager.scan(self.manager.settings())
        self.assertEqual(len(db.fetch_all("SELECT * FROM booking_attempts")), 1)
        self.adapter.backorder.assert_not_called()

    def test_monitor_settings_migrate_once_and_scan_existing_and_new_domains(self):
        db.execute("DELETE FROM booking_settings")
        db.execute("INSERT INTO query_settings (id, settings_json, updated_at) VALUES (1, ?, '')",
                   (json.dumps({"booking": self.settings}),))
        self.assertEqual(self.manager.settings(), self.settings)
        with patch.object(main, "booking_manager", self.manager):
            main.save_booking_settings(main.BookingSettingsPayload(enabled=False, schemes=[self.scheme]))
            self.assertFalse(main.get_booking_settings()["data"]["enabled"])
        self.manager.scan(self.manager.settings())
        self.assertEqual(db.fetch_all("SELECT * FROM booking_attempts"), [])
        self.manager.save_settings(self.settings)
        self.manager.scan(self.manager.settings())
        self.assertEqual(len(db.fetch_all("SELECT * FROM booking_attempts")), 1)
        db.execute("UPDATE domain_checks SET result = 'qualified' WHERE domain = 'bad.com'")
        self.manager.save_settings({"enabled": True, "schemes": [dict(self.scheme, lengths=[3])]})
        self.manager.scan(self.manager.settings())
        self.assertEqual(len(db.fetch_all("SELECT * FROM booking_attempts WHERE domain = 'bad.com'")), 1)

    def test_monitor_membership_and_current_rules_are_checked_before_submission(self):
        db.execute("INSERT INTO monitor_domain_state (domain, status, updated_at) VALUES ('abab.com', 'kicked', '')")
        self.manager.scan(self.settings)
        self.assertEqual(db.fetch_all("SELECT * FROM booking_attempts"), [])
        db.execute("UPDATE monitor_domain_state SET status = 'monitoring'")
        self.manager.scan(self.settings)
        attempts = db.fetch_all("SELECT * FROM booking_attempts")
        self.manager.save_settings(dict(self.settings, enabled=False))
        self.manager._submit(attempts[0])
        self.assertEqual(db.fetch_one("SELECT status FROM booking_attempts WHERE id = ?", (attempts[0]["id"],))["status"], "pending")
        self.manager.save_settings({"enabled": True, "schemes": [dict(self.scheme, lengths=[3])]})
        self.manager._submit(attempts[0])
        self.assertIsNone(db.fetch_one("SELECT * FROM booking_attempts WHERE id = ?", (attempts[0]["id"],)))
        self.manager.save_settings(self.settings)
        self.manager.scan(self.settings)
        attempt = db.fetch_one("SELECT * FROM booking_attempts WHERE status = 'pending'")
        db.execute("UPDATE monitor_domain_state SET status = 'kicked'")
        self.manager._submit(attempt)
        self.adapter.backorder.assert_not_called()

    def test_changes_during_channel_lookup_prevent_submission(self):
        for change in ("disabled", "removed", "rules"):
            with self.subTest(change=change):
                db.execute("DELETE FROM booking_attempts")
                db.execute("DELETE FROM monitor_domain_state")
                self.manager.save_settings(self.settings)
                self.manager.scan(self.settings)
                attempt = db.fetch_one("SELECT * FROM booking_attempts ORDER BY id LIMIT 1")

                def channels():
                    if change == "disabled":
                        self.manager.save_settings(dict(self.settings, enabled=False))
                    elif change == "removed":
                        db.execute("INSERT INTO monitor_domain_state (domain, status, updated_at) VALUES ('abab.com', 'kicked', '')")
                    else:
                        self.manager.save_settings({"enabled": True, "schemes": [dict(self.scheme, lengths=[3])]})
                    return [{"channel": 1, "status": 1, "tlds": ["com"]}]

                self.adapter.backorder_channels.side_effect = channels
                self.manager._submit(attempt)
                self.adapter.backorder.assert_not_called()
                remaining = db.fetch_one("SELECT * FROM booking_attempts WHERE id = ?", (attempt["id"],))
                if change == "disabled":
                    self.assertEqual(remaining["status"], "pending")
                else:
                    self.assertIsNone(remaining)

    def test_rejected_booking_keeps_specific_error_and_is_not_resubmitted(self):
        for response, expected in (
            ('{"code":-1,"msg":"账户余额不足"}', '账户余额不足'),
            ('{"code":-1,"message":"域名不在预订时间内"}', '域名不在预订时间内'),
            ('{"code":-1}', '{"code":-1}'),
            ('接口拒绝请求', '接口拒绝请求'),
        ):
            with self.subTest(response=response):
                db.execute("DELETE FROM booking_attempts")
                self.adapter.backorder.reset_mock()
                self.adapter.backorder.return_value = RegisterResult(False, 200, response)
                self.manager.scan(self.settings)
                self.submit_pending()
                self.manager.scan(self.settings)
                attempts = db.fetch_all("SELECT * FROM booking_attempts")
                self.assertEqual(len(attempts), 2)
                for attempt in attempts:
                    self.assertEqual((attempt['status'], attempt['error'], attempt['response']),
                                     ('failure', expected, response))
                    self.manager._submit(attempt)
                self.assertEqual(self.adapter.backorder.call_count, 2)

    def test_booked_list_shows_all_outcomes_with_domain_details_and_filters(self):
        db.execute("""UPDATE domain_checks SET wechat_status = '是', qq_status = '否',
            pollution_status = '检测失败', blocked_status = '否', blacklist_status = '否',
            filing_nature = '企业', filing_info = '测试备案信息' WHERE domain = 'abab.com'""")
        self.manager.scan(self.settings)
        for attempt in db.fetch_all("SELECT * FROM booking_attempts"):
            self.manager._submit(attempt)
        # 列表还需兼容旧版已成功提交到多个通道的历史记录。
        db.execute("""INSERT INTO booking_attempts
            (domain, registrar_api_id, registrar_name, channel, scheme_name, status, created_at)
            VALUES ('abab.com', 1, '测试 Gname', 2, '方案 1', 'success', '')""")
        db.execute("UPDATE booking_attempts SET completed_at = '2026-10-13T08:00:00+00:00', registrar_name = '测试 Gname'")
        for channel, status in enumerate(('pending', 'sending', 'failure', 'uncertain'), 3):
            db.execute("""INSERT INTO booking_attempts
                (domain, registrar_api_id, registrar_name, channel, scheme_name, status, created_at)
                VALUES ('bad.com', 1, '测试 Gname', ?, '方案 1', ?, '2026-10-12T08:00:00+00:00')""", (channel, status))
        db.execute("""UPDATE booking_attempts SET response = '{"code":-1,"msg":"账户余额不足"}',
            error = 'Gname 拒绝预订，请查看接口响应', completed_at = '2026-10-12T09:00:00+00:00' WHERE status = 'failure'""")
        db.execute("UPDATE booking_attempts SET error = '请求超时，请到 Gname 核实订单' WHERE status = 'uncertain'")
        # 列表测试不启动应用后台队列，保留各状态的固定测试数据。
        with patch.object(system_manage, 'current_user', return_value={'id': 1}):
            client = TestClient(main.app)
            self.addCleanup(client.close)
            response = client.get('/api/booked-domains', params={'page_size': 1, 'with_stats': True})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()['data']
            self.assertEqual((data['total'], data['size'], len(data['records'])), (6, 1, 1))
            row = data['records'][0]
            self.assertEqual((row['domain'], row['channel'], row['scheme_name'], row['registrar_name']),
                             ('abab.com', 2, '方案 1', '测试 Gname'))
            self.assertEqual((row['wechat_status'], row['pollution_status'], row['filing_info'], row['source']),
                             ('是', '检测失败', '测试备案信息', 'test'))
            self.assertEqual(row['booked_at'], '2026-10-13T08:00:00+00:00')
            self.assertIn({'label': '是', 'count': 2}, data['stats']['wechat_status'])
            self.assertEqual(sum(item['count'] for item in data['stats']['wechat_status']), 6)
            rows = client.get('/api/booked-domains').json()['data']['records']
            self.assertEqual([item['channel'] for item in rows], [2, 1, 5, 6, 4, 3])
            self.assertEqual({item['status']: item['booking_result'] for item in rows}, {
                'success': '成功', 'failure': '账户余额不足', 'pending': '等待提交',
                'sending': '正在提交', 'uncertain': '待核实：请求超时，请到 Gname 核实订单',
            })
            self.assertEqual(rows[-1]['booked_at'], '2026-10-12T08:00:00+00:00')
            self.assertEqual(client.get('/api/booked-domains', params={'page': 2, 'page_size': 1}).json()['data']['records'][0]['channel'], 1)
            for params, expected in (
                ({'keyword': '方案 1'}, 6), ({'keyword': '测试 Gname'}, 6), ({'keyword': '2'}, 1),
                ({'wechat_status': '否'}, 4), ({'filing_nature': '企业'}, 2),
                ({'domain_composition': 'letter', 'length': '4', 'suffix': 'com'}, 2),
                ({'suffix': 'cn'}, 0),
                ({'booked_start': '2026-10-13', 'booked_end': '2026-10-13'}, 2),
                ({'booked_start': '2026-10-12', 'booked_end': '2026-10-12'}, 4),
                ({'booked_end': '2026-10-11'}, 0),
            ):
                with self.subTest(params=params):
                    self.assertEqual(client.get('/api/booked-domains', params=params).json()['data']['total'], expected)
            self.assertEqual(client.get('/api/booked-domains', params={'booked_start': 'invalid'}).status_code, 400)
            db.execute("DELETE FROM domain_checks WHERE domain = 'abab.com'")
            data = client.get('/api/booked-domains').json()['data']
            self.assertEqual(data['total'], 6)
            self.assertEqual(data['records'][0]['filing_info'], '')

    def test_booked_menu_migration_preserves_directory_permissions_and_is_idempotent(self):
        system_manage.init_system()
        parent = db.fetch_one("SELECT id FROM sys_menus WHERE route_name = 'domain-management'")['id']
        old = db.fetch_one("SELECT id FROM sys_menus WHERE route_name = 'domain-management_booked'")['id']
        db.execute("DELETE FROM sys_role_menus WHERE menu_id = ?", (old,))
        db.execute("DELETE FROM sys_menus WHERE id = ?", (old,))
        with self.connection:
            system_manage._migrate_domain_list_menus(self.connection, db.utc_now())
            system_manage._migrate_domain_list_menus(self.connection, db.utc_now())
        menus = db.fetch_all("SELECT * FROM sys_menus WHERE route_name = 'domain-management_booked'")
        self.assertEqual(len(menus), 1)
        self.assertEqual((menus[0]['parent_id'], menus[0]['menu_name']), (parent, '预订列表'))
        parent_roles = db.fetch_all("SELECT role_id FROM sys_role_menus WHERE menu_id = ? ORDER BY role_id", (parent,))
        booked_roles = db.fetch_all("SELECT role_id FROM sys_role_menus WHERE menu_id = ? ORDER BY role_id", (menus[0]['id'],))
        self.assertEqual(booked_roles, parent_roles)


if __name__ == "__main__":
    unittest.main()
