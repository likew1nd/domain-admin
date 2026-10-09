import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, main
from app.monitor_service import MonitorManager
from app.query_service import QueryTaskManager


class DomainDateTests(unittest.TestCase):
    def test_timestamps_survive_storage_snapshots_and_inclusive_list_filters(self):
        connection = sqlite3.connect(':memory:', check_same_thread=False)
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.create_function('reverse', 1, lambda value: str(value)[::-1])
        connection.create_function('label_kind_of', 1, db.label_kind)
        self.enterContext(patch.object(db, 'get_connection', return_value=connection))
        self.enterContext(patch.object(main.system_manage, 'current_user', return_value={'id': 1}))
        db.init_db()
        db.execute(
            "INSERT INTO sources (id, source_key, name, adapter, base_url, download_template, created_at, updated_at) "
            "VALUES (1, 'test', '测试来源', 'generic', '', '', '', '')"
        )
        db.upsert_domains({'id': 1, 'name': '测试来源'}, '2026-10-09', {'example.com'})
        db.execute(
            "INSERT INTO query_tasks (id, name, filters_json, proxy_json, status, created_at, updated_at) "
            "VALUES (1, '查询任务', '{}', '{}', 'completed', '', '')"
        )
        creation = '2020-01-01T23:59:59+08:00'
        expiration = '2026-10-09T23:59:59+08:00'
        info = {'creation_date': creation, 'expiration_date': expiration, 'statuses': ['pendingDelete']}
        statuses = {key: '否' for key in ('qq_status', 'wechat_status', 'blocked_status', 'pollution_status', 'blacklist_status')}
        client = TestClient(main.app)
        self.addCleanup(client.close)
        filters = {'registration_start': '2020-01-01', 'registration_end': '2020-01-01',
                   'expiration_start': '2026-10-09', 'expiration_end': '2026-10-09', 'with_stats': True}
        for result in ('qualified', 'unqualified'):
            QueryTaskManager._save_result(1, 'example.com', result, '', info, {}, statuses)
            for url, params in (('/api/domains', filters), ('/api/query-results', {**filters, 'result': result})):
                with self.subTest(url=url, result=result):
                    response = client.get(url, params=params)
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()['data']
                    self.assertEqual(data['total'], 1)
                    self.assertEqual(data['records'][0]['creation_date'], creation)
                    self.assertEqual(data['records'][0]['expiration_date'], expiration)
                    for field in ('registration_end', 'expiration_end'):
                        excluded = client.get(url, params={**params, field: '2019-12-31'}).json()['data']
                        self.assertEqual(excluded['total'], 0)
        MonitorManager._record_registered('example.com', {'id': 1, 'name': '测试注册商'}, '')
        MonitorManager._kick('example.com', '测试', '')
        for table in ('domain_checks', 'kicked_domains', 'registered_domains'):
            row = db.fetch_one(f'SELECT creation_date, expiration_date FROM {table}')
            self.assertEqual(row, {'creation_date': creation, 'expiration_date': expiration})
        response = client.get('/api/kicked-domains', params=filters)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data']['total'], 1)
        for field in ('registration_end', 'expiration_end'):
            response = client.get('/api/kicked-domains', params={**filters, field: '2019-12-31'})
            self.assertEqual(response.json()['data']['total'], 0)


if __name__ == '__main__':
    unittest.main()
