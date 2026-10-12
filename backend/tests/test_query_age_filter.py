import asyncio
import json
import sqlite3
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, main
from app.query_service import QueryTaskManager


class QueryAgeFilterTests(unittest.TestCase):
    def setUp(self):
        connection = sqlite3.connect(':memory:', check_same_thread=False)
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.create_function('reverse', 1, lambda value: str(value)[::-1])
        connection.create_function('label_kind_of', 1, db.label_kind)
        self.enterContext(patch.object(db, 'get_connection', return_value=connection))
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'test', '测试来源', '', '', '', '')"
        )
        for date, domains in (
            ('2026-10-09', {'old.com'}),
            ('2026-10-10', {'boundary.com'}),
            ('2026-10-11', {'yesterday.com'}),
            ('2026-10-12', {'today.com', 'checked.com', 'other.net'}),
            ('2026-10-13', {'future.com'}),
        ):
            db.upsert_domains({'id': 1, 'name': '测试来源'}, date, domains)
        db.execute("UPDATE domains SET query_time = '2026-10-12' WHERE domain = 'checked.com'")
        self.manager = QueryTaskManager()
        self.addCleanup(self.manager._executor.shutdown, wait=False, cancel_futures=True)
        self.enterContext(patch.object(main, 'query_manager', self.manager))
        self.clock = self.enterContext(patch('app.query_service.datetime', wraps=datetime))
        # UTC 仍是 11 日，北京已经是 12 日；不依赖机器时区或实际日期。
        self.clock.now.side_effect = lambda tz: self.instant.astimezone(tz)
        self.instant = datetime(2026, 10, 11, 16, 30, tzinfo=timezone.utc)

    def test_calendar_days_include_both_boundaries_and_match_preview(self):
        for days, expected in (
            (2, {'today.com', 'yesterday.com', 'boundary.com'}),
            (0, {'today.com'}),
            (None, {'today.com', 'yesterday.com', 'boundary.com', 'old.com', 'future.com'}),
        ):
            with self.subTest(days=days):
                payload = main.QueryPreviewPayload(joined_within_days=days, suffixes=['com'])
                self.assertEqual(main.preview_query_task(payload)['data']['total'], len(expected))
                for random_query in (False, True):
                    rows = self.manager._candidates(payload.model_dump(), 100, random_query)
                    self.assertEqual({row['domain'] for row in rows}, expected)
        # 持续任务每次选取候选时重新计算日期，跨天后排除 10 日的数据。
        self.instant = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
        rows = self.manager._candidates({'joined_within_days': 2, 'suffixes': ['com']}, 100)
        self.assertEqual({row['domain'] for row in rows}, {'yesterday.com', 'today.com', 'future.com'})

    def test_window_crosses_month_and_year_boundaries(self):
        self.instant = datetime(2025, 12, 31, 16, 0, tzinfo=timezone.utc)
        for date in ('2025-12-29', '2025-12-30', '2025-12-31', '2026-01-01'):
            db.upsert_domains({'id': 1, 'name': '测试来源'}, date, {f'{date}.org'})
        rows = self.manager._candidates({'joined_within_days': 2}, 100)
        self.assertEqual({row['domain'] for row in rows}, {'2025-12-30.org', '2025-12-31.org', '2026-01-01.org'})

    def test_saved_settings_task_storage_and_restart_preserve_window(self):
        with (
            patch.object(main.apihz_client, 'has_api_key', return_value=False),
            patch.object(self.manager._executor, 'submit') as submit,
        ):
            self.assertIsNone(main.QueryTaskPayload().joined_within_days)
            for days in (2, 0, None):
                with self.subTest(days=days):
                    payload = main.QueryTaskPayload(joined_within_days=days)
                    main.save_query_settings(payload)
                    self.assertEqual(main.get_query_settings()['data']['settings']['joined_within_days'], days)
                    task = main.create_query_task(payload)['data']
                    stored = db.fetch_one('SELECT filters_json FROM query_tasks WHERE id = ?', (task['id'],))
                    self.assertEqual(json.loads(stored['filters_json'])['joined_within_days'], days)
                    self.manager._runtimes.clear()
                    submit.reset_mock()
                    self.manager.resume_all()
                    self.assertEqual(submit.call_args.args[1], task['id'])
                    db.execute("UPDATE query_tasks SET status = 'completed' WHERE id = ?", (task['id'],))

    def test_both_api_payloads_reject_invalid_day_counts(self):
        for model in (main.QueryTaskPayload, main.QueryPreviewPayload):
            for days in (-1, 1.5, 36501, 'invalid'):
                with self.subTest(model=model.__name__, days=days):
                    with self.assertRaises(ValidationError):
                        model(joined_within_days=days)

    def test_random_buffer_drops_expired_candidates_after_midnight(self):
        db.execute('DELETE FROM domains')
        old_domains = {f'a{index:02}.com' for index in range(25)}
        db.upsert_domains({'id': 1, 'name': '测试来源'}, '2026-10-10', old_domains)
        db.upsert_domains({'id': 1, 'name': '测试来源'}, '2026-10-12', {'znew.com'})
        queried = []
        candidates = self.manager._candidates

        def ordered_sample(*args):
            return sorted(candidates(*args), key=lambda row: row['domain'])

        async def lookup(domain, *args):
            # 第一批已入队；其余随机缓存中的 10 日域名跨天后不应再入队。
            self.instant = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
            return {}

        def save_result(task_id, domain, *args):
            queried.append(domain)
            db.execute("UPDATE domains SET query_time = 'checked' WHERE domain = ?", (domain,))

        with (
            patch.object(self.manager._executor, 'submit'),
            patch.object(self.manager, '_candidates', side_effect=ordered_sample),
            patch.object(self.manager, '_lookup_whois', side_effect=lookup),
            patch.object(self.manager, '_save_result', side_effect=save_result),
            patch('app.query_service.match_filters', return_value=(False, '测试')),
        ):
            task = main.create_query_task(main.QueryTaskPayload(
                joined_within_days=2, random_query=True, continuous=False, threads=1,
            ))['data']
            asyncio.run(asyncio.wait_for(self.manager._run(task['id'], threading.Event()), timeout=3))
        self.assertEqual(set(queried), {f'a{index:02}.com' for index in range(20)} | {'znew.com'})


if __name__ == '__main__':
    unittest.main()
