import unittest
import asyncio
import sys
from unittest.mock import patch
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whois.exceptions import WhoisDomainNotFoundError

from app.whois_client import lookup, match_filters
from app.query_service import QueryTaskManager


class WhoisFilterTests(unittest.TestCase):
    @patch("app.whois_client.whois.whois", side_effect=WhoisDomainNotFoundError('No match for "example.com"'))
    def test_whois_not_found_is_available(self, _whois):
        info = lookup("example.com")

        self.assertTrue(info["available"])
        self.assertEqual(info["expiration_date"], "")

    def test_available_domain_without_dates_is_eligible_for_icp(self):
        info = {"available": True, "expiration_date": "", "creation_date": "", "statuses": []}

        self.assertTrue(match_filters(info, {"delete_type": "expired"})[0])
        self.assertFalse(match_filters(info, {"delete_type": "all", "expiration_start": "2020-01-01"})[0])
        self.assertEqual(QueryTaskManager._deletion_status(info), "可注册")

    @patch(
        "app.whois_client._lookup_socks",
        return_value=SimpleNamespace(
            expiration_date="2020-01-01", creation_date="2010-01-01", status="pendingDelete", raw="raw"
        ),
    )
    def test_whois_proxy_is_forwarded(self, socks_lookup):
        lookup("example.com", "socks5h://127.0.0.1:1080")
        socks_lookup.assert_called_once_with("example.com", "socks5h://127.0.0.1:1080")

    @patch(
        "app.query_service.lookup",
        side_effect=[RuntimeError("bad proxy"), {"creation_date": "2020-01-01", "statuses": ["pendingDelete"]}],
    )
    def test_whois_retry_rotates_proxy(self, whois_lookup):
        class Pool:
            def __init__(self):
                self.proxies = iter(["bad:1", "good:2"])
                self.reports = []

            async def acquire(self):
                return next(self.proxies)

            async def report(self, proxy, success):
                self.reports.append((proxy, success))

        pool = Pool()
        result = asyncio.run(QueryTaskManager._lookup_whois("example.com", 2, proxy_pool=pool))

        self.assertEqual(result["statuses"], ["pendingDelete"])
        self.assertEqual(pool.reports, [("bad:1", False), ("good:2", True)])
        self.assertEqual(whois_lookup.call_count, 2)

    def test_all_delete_types_keeps_date_filters(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        info = {"expiration_date": future, "creation_date": "2020-01-01", "statuses": []}

        self.assertFalse(match_filters(info, {"delete_type": "all"})[0])
        self.assertFalse(match_filters(info, {"delete_type": "expired"})[0])
        self.assertFalse(match_filters(info, {"delete_type": "all", "expiration_end": date.today().isoformat()})[0])

    def test_expired_states_are_eligible_without_expiration_date(self):
        for status, delete_type in ((["redemptionPeriod"], "redemption"), (["pendingDelete"], "pending_delete")):
            info = {"expiration_date": "", "creation_date": "2020-01-01", "statuses": status}
            self.assertTrue(match_filters(info, {"delete_type": delete_type})[0])
            self.assertTrue(match_filters(info, {"delete_type": "all"})[0])

    def test_filing_status_distinguishes_no_record_from_not_queried(self):
        self.assertEqual(QueryTaskManager._filing_info({"queried": True, "records": []}), ("未备案", ""))
        self.assertEqual(QueryTaskManager._filing_info({"queried": False, "records": []}), ("未查询", ""))
        self.assertEqual(
            QueryTaskManager._filing_info({"queried": True, "error": "验证码识别失败"}),
            ("查询失败", "验证码识别失败"),
        )
        self.assertEqual(QueryTaskManager._filing_info({"queried": True, "records": [{}]}), ("已备案", ""))

    @patch("app.query_service.lookup", return_value={"expiration_date": "", "creation_date": "", "statuses": []})
    def test_empty_whois_result_is_retried_and_not_accepted(self, _lookup):
        with self.assertRaisesRegex(RuntimeError, "WHOIS 返回信息不足"):
            asyncio.run(QueryTaskManager._lookup_whois("example.com", 2))
        self.assertEqual(_lookup.call_count, 2)


if __name__ == "__main__":
    unittest.main()
