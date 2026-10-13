import sys
import unittest
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources.aliyun import AliyunAdapter
from app.sources.base import SourceError, parse_domains
from app.sources.registry import create_adapter


class AliyunSourceTests(unittest.TestCase):
    def adapter(self, handler, suffixes=None, cookie="test-cookie"):
        adapter = create_adapter({
            "source_key": "aliyun", "adapter": "aliyun",
            "base_url": "https://domainapi.aliyun.com",
            "download_template": "/partnerReserve/snatchExport.json",
            "collection_suffixes": suffixes or [],
        }, cookie)
        adapter.client.close()
        adapter.client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(adapter.close)
        self.assertIsInstance(adapter, AliyunAdapter)
        return adapter

    def test_authenticated_date_and_suffix_requests_merge_utf8_domains(self):
        requests = []

        def respond(request):
            requests.append(request)
            content = "Example.COM\n凭证.com\n" if request.url.params["suffixes"] == "com" else "second.net\nexample.com\n"
            return httpx.Response(200, text=content, headers={"content-type": "text/plain;charset=UTF-8"})

        adapter = self.adapter(respond, ["com", "net"])
        downloaded = adapter.download("2026-10-13", "txt")
        self.assertEqual(parse_domains(downloaded.content, encoding=downloaded.encoding), {"example.com", "凭证.com", "second.net"})
        self.assertEqual(downloaded.filename, "aliyun-2026-10-13.txt")
        self.assertEqual(len(requests), 2)
        for request, suffix in zip(requests, ["com", "net"]):
            self.assertEqual(request.url.path, "/partnerReserve/snatchExport.json")
            self.assertEqual(request.url.params["suffixes"], suffix)
            self.assertEqual(request.url.params["releaseDate"], "2026-10-13")
            self.assertEqual(request.url.params["originReleaseDate"], "2026-10-13")
            self.assertEqual(request.url.params["quickSearch"], '[""]')
            self.assertEqual(request.url.params["fetchSearchTotal"], "true")
            self.assertEqual(request.headers["Cookie"], "test-cookie")
            self.assertEqual(request.headers["Referer"], "https://mi.aliyun.com/")

    def test_default_suffix_and_today(self):
        def respond(request):
            self.assertEqual(request.url.params["suffixes"], "com")
            return httpx.Response(200, text="example.com\n")
        adapter = self.adapter(respond)
        self.assertEqual(adapter.available_dates(), [date.today().isoformat()])
        self.assertEqual(parse_domains(adapter.download("2026-10-13", "txt").content), {"example.com"})

    def test_missing_cookie_and_csv_fail_before_requests(self):
        def unexpected(_request):
            self.fail("No request should be sent")
        adapter = self.adapter(unexpected, cookie="")
        with self.assertRaisesRegex(SourceError, "Cookie"):
            adapter.available_dates()
        with self.assertRaisesRegex(SourceError, "Cookie"):
            adapter.download("2026-10-13", "txt")
        adapter = self.adapter(unexpected)
        with self.assertRaisesRegex(SourceError, "TXT"):
            adapter.download("2026-10-13", "csv")

    def test_http_200_login_and_other_json_errors_cannot_be_imported(self):
        for payload, expected in (
            ({"code": "500", "success": False, "message": "请先登录再使用结果导出功能"}, "Cookie 已失效"),
            ({"code": "500", "success": False, "message": "导出过于频繁"}, "导出过于频繁"),
        ):
            with self.subTest(payload=payload):
                adapter = self.adapter(lambda _request: httpx.Response(200, json=payload))
                with self.assertRaisesRegex(SourceError, expected):
                    adapter.download("2026-10-13", "txt")
        adapter = self.adapter(lambda _request: httpx.Response(200, text='{"message":"请先登录再使用结果导出功能"}'))
        with self.assertRaisesRegex(SourceError, "Cookie 已失效"):
            adapter.download("2026-10-13", "txt")

    def test_html_and_export_limit_are_rejected(self):
        for content, content_type, expected in (
            ("<html><script>window.dataLayer</script></html>", "text/html", "网页"),
            ("\n".join(f"domain{i}.com" for i in range(10_000)), "text/plain", "10000"),
        ):
            with self.subTest(expected=expected):
                adapter = self.adapter(lambda _request: httpx.Response(200, text=content, headers={"content-type": content_type}))
                with self.assertRaisesRegex(SourceError, expected):
                    adapter.download("2026-10-13", "txt")


if __name__ == "__main__":
    unittest.main()
