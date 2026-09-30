import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.base import normalize_domain, parse_domains


class ImporterTests(unittest.TestCase):
    def test_normalize_domain(self):
        self.assertEqual(normalize_domain("HTTPS://Example.COM/path"), "example.com")
        self.assertIsNone(normalize_domain("not a domain"))

    def test_parse_txt_deduplicates(self):
        data = b"Example.com\nexample.com\nsecond.net\n"
        self.assertEqual(parse_domains(data), {"example.com", "second.net"})

    def test_parse_csv_uses_domain_cells(self):
        data = "domain,status\nfirst.io,ok\nsecond.cn,ok\n".encode()
        self.assertEqual(parse_domains(data, "csv", "csv"), {"first.io", "second.cn"})


if __name__ == "__main__":
    unittest.main()
