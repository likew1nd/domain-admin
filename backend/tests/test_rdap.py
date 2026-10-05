import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.whois_client import RdapIncompleteError, _lookup_rdap, lookup


class RdapLookupTests(unittest.TestCase):
    def test_rdap_bootstrap_and_events_are_parsed(self):
        bootstrap = {
            "services": [[["com"], ["https://rdap.example.test/com/v1"]]],
        }
        record = {
            "ldhName": "example.com",
            "status": ["active"],
            "events": [
                {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
                {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
            ],
        }
        with (
            patch("app.whois_client._rdap_bootstrap", {}),
            patch("app.whois_client._rdap_bootstrap_loaded_at", 0),
            patch(
                "app.whois_client._http_json",
                side_effect=[(bootstrap, "bootstrap"), (record, "record")],
            ) as request,
        ):
            result = _lookup_rdap("example.com", "192.0.2.10:8080")

        self.assertEqual(result["expiration_date"], "2030-01-01")
        self.assertEqual(result["creation_date"], "2020-01-01")
        self.assertEqual(result["statuses"], ["active"])
        self.assertEqual(request.call_args_list[1].args[1], "192.0.2.10:8080")
        self.assertEqual(request.call_args_list[1].args[0], "https://rdap.example.test/com/v1/domain/example.com")

    def test_lookup_prefers_rdap(self):
        rdap_info = {
            "domain": "example.com",
            "expiration_date": "2030-01-01",
            "creation_date": "2020-01-01",
            "status": "active",
            "statuses": ["active"],
            "available": False,
            "raw": "{}",
        }
        with (
            patch("app.whois_client._lookup_rdap", return_value=rdap_info),
            patch("app.whois_client.whois.whois") as whois_lookup,
        ):
            result = lookup("example.com")

        self.assertEqual(result, rdap_info)
        whois_lookup.assert_not_called()

    def test_incomplete_rdap_falls_back_to_python_whois(self):
        whois_result = type(
            "WhoisResult",
            (),
            {
                "expiration_date": "2020-01-01",
                "creation_date": "2010-01-01",
                "status": "pendingDelete",
                "registrar": "Example Registrar",
                "name_servers": [],
                "raw": "raw",
            },
        )()
        with (
            patch("app.whois_client._lookup_rdap", side_effect=RdapIncompleteError("缺少到期时间")),
            patch("app.whois_client.whois.whois", return_value=whois_result) as whois_lookup,
        ):
            result = lookup("example.com")

        self.assertEqual(result["expiration_date"], "2020-01-01")
        self.assertEqual(result["statuses"], ["pendingDelete"])
        whois_lookup.assert_called_once_with("example.com", inc_raw=True)


if __name__ == "__main__":
    unittest.main()
