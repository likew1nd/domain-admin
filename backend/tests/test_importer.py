import unittest
import sys
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.base import SourceAdapter, SourceError, ensure_authenticated, normalize_domain, parse_domains


class ImporterTests(unittest.TestCase):
    def test_normalize_domain(self):
        self.assertEqual(normalize_domain("HTTPS://Example.COM/path"), "example.com")
        self.assertIsNone(normalize_domain("not a domain"))

    def test_normalize_international_domain_preserves_body_and_rejects_invalid_labels(self):
        self.assertEqual(normalize_domain("中文.中国"), "中文.中国")
        self.assertEqual(normalize_domain("idn.XN--FIQS8S"), "idn.xn--fiqs8s")
        self.assertEqual(normalize_domain("HTTPS://中文.COM./path"), "中文.com")
        self.assertIsNone(normalize_domain("bad_underscore.com"))
        self.assertIsNone(normalize_domain(f"{'a' * 64}.com"))
        self.assertIsNone(normalize_domain("example..com"))

    def test_parse_txt_deduplicates(self):
        data = b"Example.com\nexample.com\nsecond.net\n"
        self.assertEqual(parse_domains(data), {"example.com", "second.net"})

    def test_parse_csv_uses_domain_cells(self):
        data = "domain,status\nfirst.io,ok\nsecond.cn,ok\n".encode()
        self.assertEqual(parse_domains(data, "csv", "csv"), {"first.io", "second.cn"})

    def test_html_and_scripts_cannot_be_imported_as_domain_lists_even_in_a_zip(self):
        html = b'<!doctype html><html><script>\nwindow.dataLayer\ns.async\ns.src\njquery.init\n</script></html>'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("domains.txt", html)
        for content in (html, archive.getvalue(), b"const layer = window.dataLayer;\ns.async\n", b"(function(){\nwindow.jquery\n})();"):
            with self.subTest(content=content[:30]), self.assertRaisesRegex(SourceError, "网页或脚本"):
                parse_domains(content)
        self.assertEqual(parse_domains(b"function.com\nconst.net\nwindow.cn\n"), {"function.com", "const.net", "window.cn"})

    def test_login_detection_supports_utf8_gb18030_and_normal_pages_with_login_links(self):
        for title, encoding in (("账号登录", "utf-8"), ("会员登陆", "gb18030"), ("Gname Account Login", "utf-8")):
            raw = f'<html><title class="main">{title}</title><script>window.jquery</script></html>'.encode(encoding)
            with self.subTest(title=title):
                with self.assertRaisesRegex(SourceError, "Cookie"):
                    ensure_authenticated(httpx.Response(200, content=raw))
                with self.assertRaisesRegex(SourceError, "登录页面.*Cookie"):
                    parse_domains(raw)
        normal_page = '<html><title>过期域名列表</title><a href="/login">请登录</a><span data-sj="2026-01-01">日期</span></html>'.encode()
        ensure_authenticated(httpx.Response(200, content=normal_page))

    def test_download_rejects_html_and_javascript_response_types(self):
        adapter = SourceAdapter({"base_url": "https://example.com", "download_template": "/domains.txt", "source_key": "test"}, "")
        self.addCleanup(adapter.close)
        for content_type in ("text/html; charset=UTF-8", "application/javascript"):
            response = httpx.Response(200, content=b"window.dataLayer", headers={"content-type": content_type})
            with self.subTest(content_type=content_type), patch.object(adapter.client, "get", return_value=response):
                with self.assertRaisesRegex(SourceError, "网页或脚本"):
                    adapter.download("2026-01-01", "txt")


if __name__ == "__main__":
    unittest.main()
