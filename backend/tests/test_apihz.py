import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import apihz_client
from app.query_service import QueryTaskManager


class ApiHzClientTests(unittest.TestCase):
    def test_whois_minute_precision_slashes_and_date_only(self):
        payload = {'code': 200, 'whois': (
            'Creation Date: 2020/01/01 17:27<br>'
            'Expiration Date: 2027-01-01<br>Domain Status: ok'
        )}
        result = apihz_client._parse_whois('example.com', payload, '')
        self.assertEqual(result['creation_date'], '2020-01-01T17:27:00')
        self.assertEqual(result['expiration_date'], '2027-01-01')

    def test_lookup_parses_whois_html(self):
        payload = {
            "code": 200,
            "whois": (
                "Domain Name: EXAMPLE.COM<br>"
                "Creation Date: 1995-08-14T04:00:00Z<br>"
                "Registry Expiry Date: 2027-08-13T04:00:00Z<br>"
                "Domain Status: clientDeleteProhibited https://icann.org/epp"
            ),
        }
        with patch("app.apihz_client._request", return_value=(payload, "raw")):
            result = apihz_client.lookup("Example.COM", "123", "key")

        self.assertEqual(result["source"], "apihz")
        self.assertEqual(result["expiration_date"], "2027-08-13T12:00:00+08:00")
        self.assertEqual(result["creation_date"], "1995-08-14T12:00:00+08:00")
        self.assertEqual(result["statuses"], ["clientDeleteProhibited"])

    def test_lookup_recognises_no_matching_record_as_available(self):
        with patch(
            "app.apihz_client._request",
            return_value=({"code": 200, "whois": "No matching record.<br>"}, "raw"),
        ):
            result = apihz_client.lookup("example.com", "123", "key")

        self.assertTrue(result["available"])
        self.assertEqual(result["expiration_date"], "")

    def test_lookup_parses_api_box_registration_and_expiration_time(self):
        payload = {
            "code": 200,
            "whois": (
                "Domain Name: 015580.CN<br>"
                "Domain Status: ok<br>"
                "Registration Time: 2026-10-05 17:27:22<br>"
                "Expiration Time: 2027-10-05 17:27:22<br>"
            ),
        }
        with patch("app.apihz_client._request", return_value=(payload, "raw")):
            result = apihz_client.lookup("015580.cn", "123", "key")

        self.assertEqual(result["creation_date"], "2026-10-05T17:27:22")
        self.assertEqual(result["expiration_date"], "2027-10-05T17:27:22")
        self.assertEqual(result["statuses"], ["ok"])

    def test_lookup_reports_api_error(self):
        with patch(
            "app.apihz_client._request",
            return_value=({"code": 400, "msg": "KEY 无效"}, "raw"),
        ):
            with self.assertRaisesRegex(apihz_client.ApiHzError, "KEY 无效"):
                apihz_client.lookup("example.com", "123", "key")

    def test_lookup_uses_rdap_then_whois_only_for_unsupported_suffix(self):
        rdap_error = {"code": 403, "msg": "RDAP 不支持该后缀"}
        whois_payload = {
            "code": 200,
            "whois": "Domain Name: EXAMPLE.CN<br>Domain Status: ok<br>Expiration Date: 2027-01-01",
        }
        with patch(
            "app.apihz_client._request",
            side_effect=[(rdap_error, "rdap"), (whois_payload, "whois")],
        ) as request:
            result = apihz_client.lookup("example.cn", "123", "key")

        self.assertEqual(result["source"], "apihz")
        self.assertEqual(result["expiration_date"], "2027-01-01")
        self.assertEqual(request.call_args_list[0].args[3], apihz_client.RDAP_API_URL)
        self.assertEqual(request.call_args_list[1].args[3], apihz_client.WHOIS_API_URL)

    def test_lookup_falls_back_to_whois_for_rdap_400(self):
        whois_payload = {
            "code": 200,
            "whois": "Domain Name: EXAMPLE.VIP<br>Domain Status: ok<br>Expiration Date: 2027-01-01",
        }
        with patch(
            "app.apihz_client._request",
            side_effect=[
                ({"code": 400, "msg": "查询失败，请重试"}, "rdap"),
                (whois_payload, "whois"),
            ],
        ) as request:
            result = apihz_client.lookup("example.vip", "123", "key")

        self.assertEqual(result["expiration_date"], "2027-01-01")
        self.assertEqual(request.call_args_list[0].args[3], apihz_client.RDAP_API_URL)
        self.assertEqual(request.call_args_list[1].args[3], apihz_client.WHOIS_API_URL)

    def test_lookup_parses_rdap_payload(self):
        payload = {
            "objectClassName": "domain",
            "ldhName": "EXAMPLE.COM",
            "status": ["client transfer prohibited"],
            "events": [
                {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
                {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
            ],
        }
        with patch("app.apihz_client._request", return_value=(payload, "raw")):
            result = apihz_client.lookup("example.com", "123", "key")

        self.assertEqual(result["expiration_date"], "2027-08-13T12:00:00+08:00")
        self.assertEqual(result["creation_date"], "1995-08-14T12:00:00+08:00")
        self.assertEqual(result["statuses"], ["client transfer prohibited"])

    def test_lookup_treats_whois_no_data_as_available(self):
        with patch(
            "app.apihz_client._request",
            side_effect=[
                ({"code": 403, "msg": "unsupported"}, "rdap"),
                ({"code": 200, "whois": "No Data Found<br>"}, "whois"),
            ],
        ):
            result = apihz_client.lookup("0019aaa.vip", "123", "key")

        self.assertTrue(result["available"])

    def test_request_defaults_to_registry_lookup(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"code": 400, "msg": "error"}'

        class Opener:
            request = None

            def open(self, request, timeout):
                self.request = request
                return Response()

        opener = Opener()
        with patch("app.apihz_client.build_opener", return_value=opener):
            apihz_client._request("example.com", "123", "key")

        self.assertIn("type=2", opener.request.full_url)
        self.assertIn("vip.apihz.cn", opener.request.full_url)

    def test_request_normalises_numeric_error_response(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"400"

        class Opener:
            def open(self, request, timeout):
                return Response()

        with patch("app.apihz_client.build_opener", return_value=Opener()):
            payload, _ = apihz_client._request("example.vip", "123", "key")

        self.assertEqual(payload["code"], 400)

    def test_query_task_uses_api_box_without_proxy(self):
        info = {"expiration_date": "2020-01-01", "statuses": ["pendingDelete"], "source": "apihz"}
        with (
            patch("app.query_service.apihz_client.lookup", return_value=info) as lookup,
            patch("app.query_service.apihz_client.get_api_key", return_value="key"),
        ):
            result = asyncio.run(QueryTaskManager._lookup_whois("example.com", 1, source="apihz", apihz_id="123"))

        self.assertEqual(result, info)
        lookup.assert_called_once_with("example.com", "123", "key")


if __name__ == "__main__":
    unittest.main()
