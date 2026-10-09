import base64
import hashlib
import hmac
import json
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sys
from unittest.mock import Mock, patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registrar_adapters import (
    AliyunIntlRegistrar,
    DynadotRegistrar,
    GodaddyRegistrar,
    HttpJsonRegistrar,
    RegistrarError,
    _availability_result,
    create_adapter,
)
import app.registrar_adapters as registrar_adapters


class RegistrarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(registrar_adapters._dynadot_states, clear=True))

    def test_dynadot_parallel_availability_queries_do_not_burst(self):
        clock = [100.0]
        request_times = []

        def sleep(seconds):
            clock[0] += seconds

        def connect(*args, **kwargs):
            response = Mock()
            response.read.return_value = b'{"available":false}'
            connection = Mock()
            connection.getresponse.return_value = response

            def send(*args, **kwargs):
                limited = bool(request_times and clock[0] - request_times[-1] < 1.0)
                request_times.append(clock[0])
                response.status = 429 if limited else 200

            connection.request.side_effect = send
            return connection

        def check(domain):
            return DynadotRegistrar({}, "burst-key:secret").check_available(domain)

        with (
            patch.object(time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(time, "sleep", side_effect=sleep),
            patch.object(registrar_adapters.http.client, "HTTPSConnection", side_effect=connect),
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            results = list(pool.map(check, ["guiyouzs.com", "czicpc.com", "haijingwenhua.com", "inongpu.com"]))
        self.assertEqual([result.status_code for result in results], [200] * 4)
        self.assertEqual([result.available for result in results], [False] * 4)

    def test_dynadot_cooldown_is_shared_and_respects_retry_after(self):
        clock = [100.0]
        adapter = DynadotRegistrar({}, "api-key:secret")
        with (
            patch.object(time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(time, "sleep") as sleep,
            patch.object(registrar_adapters.http.client, "HTTPSConnection") as connect,
        ):
            connection = connect.return_value
            response = connection.getresponse.return_value
            response.status = 429
            response.read.return_value = b'{}'
            response.getheader.return_value = "45"
            with self.assertRaisesRegex(RegistrarError, "429.*45"):
                adapter.check_available("a.test")
            connection.request.assert_called_once()
            connection.close.assert_called_once()

            # Fresh adapters and all DY operations must observe the same cooldown.
            for operation in (
                lambda: DynadotRegistrar({}, "api-key:secret").check_available("b.test"),
                adapter.test,
                lambda: adapter.register("a.test"),
            ):
                with self.assertRaisesRegex(RegistrarError, "冷却中"):
                    operation()
            self.assertEqual(connect.call_count, 1)

            # Independent keys and the sandbox are not blocked by this account.
            response.status = 200
            response.read.return_value = b'{"available":false}'
            self.assertFalse(DynadotRegistrar({}, "other-key:secret").check_available("c.test").available)
            sandbox = DynadotRegistrar({"endpoint": DynadotRegistrar.sandbox_endpoint}, "api-key:secret")
            self.assertFalse(sandbox.check_available("d.test").available)
            clock[0] = 144.0
            with self.assertRaisesRegex(RegistrarError, "约 1 秒"):
                adapter.check_available("a.test")
            self.assertEqual(connect.call_count, 3)

            clock[0] = 145.0
            self.assertFalse(adapter.check_available("a.test").available)
            self.assertEqual(connect.call_count, 4)
            sleep.assert_not_called()

    def test_dynadot_repeated_429_backs_off_and_success_resets_delay(self):
        clock = [100.0]
        adapter = DynadotRegistrar({}, "api-key:secret")
        with (
            patch.object(time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(time, "sleep") as sleep,
            patch.object(registrar_adapters.http.client, "HTTPSConnection") as connect,
        ):
            response = connect.return_value.getresponse.return_value
            response.status = 429
            response.read.return_value = b'{}'
            response.getheader.return_value = "invalid"
            for delay in (5, 10, 20, 40, 60, 60):
                with self.assertRaisesRegex(RegistrarError, f"冷却 {delay} 秒"):
                    adapter.check_available("a.test")
                clock[0] += delay
            self.assertEqual(connect.call_count, 6)  # No implicit retries, including for POST.
            response.status = 200
            response.read.return_value = b'{"available":false}'
            self.assertFalse(adapter.check_available("a.test").available)
            clock[0] += 1
            response.status = 429
            with self.assertRaisesRegex(RegistrarError, "冷却 5 秒"):
                adapter.register("a.test")
            self.assertEqual(connect.call_count, 8)
            sleep.assert_not_called()

    def test_dynadot_retry_after_parses_seconds_and_http_dates(self):
        now = datetime(2026, 10, 8, 6, 0, 0, tzinfo=timezone.utc)
        cases = (
            ("120", 120),
            (format_datetime(now + timedelta(seconds=90), usegmt=True), 90),
            (format_datetime(now - timedelta(seconds=10), usegmt=True), 0),
            (None, 0),
            ("invalid", 0),
            ("-3", 0),
        )
        with patch.object(registrar_adapters, "datetime") as date:
            date.now.return_value = now
            for value, expected in cases:
                with self.subTest(value=value):
                    self.assertEqual(registrar_adapters._retry_after_seconds(value), expected)

    def test_dynadot_test_rejects_invalid_key_with_http_200(self):
        adapter = DynadotRegistrar({}, "api-key:api-secret")
        with patch.object(adapter, "_request", return_value=(200, '{"code":401,"message":"Unauthorized"}')):
            result = adapter.test()
        self.assertFalse(result[0])

    def test_dynadot_registration_sends_canonical_content_type(self):
        # Replay the gateway rejection saved for ip3t.com on 2026-10-09.
        rejection = {
            "code": 400,
            "message": "Bad Request",
            "error": {"description": "Unsupported content-type in the header. The Content-Type header must be set to 'application/json'."},
        }
        with patch.object(registrar_adapters.http.client, "HTTPSConnection") as connect:
            connection = connect.return_value
            response = connection.getresponse.return_value
            response.status = 200
            response.read.side_effect = lambda: json.dumps(
                {"code": 200, "message": "Success"}
                if connection.request.call_args.kwargs["headers"].get("Content-Type") == "application/json"
                else rejection
            ).encode()
            result = DynadotRegistrar({"config": {"payload": {}, "currency": "USD"}}, "api-key:api-secret").register("ip3t.com")
        self.assertTrue(result.success, result.response)
        sent = connection.request.call_args
        self.assertEqual(sent.args, ("POST", "/restful/v2/domains/ip3t.com/register"))
        self.assertEqual(json.loads(sent.kwargs["body"]), {"domain": {"duration": 1, "privacy": "full"}, "currency": "USD"})

    def test_dynadot_registration_supplies_required_domain_parameters(self):
        adapter = DynadotRegistrar({"config_json": '{"payload":{},"currency":"USD"}'}, "api-key:api-secret")

        def register_response(method, path, payload):
            for field in ("privacy", "duration"):
                if field not in payload.get("domain", {}):
                    return 400, json.dumps({"code": 400, "message": f"The required parameter {field} is missing."})
            self.assertEqual(payload["domain"], {"privacy": "full", "duration": 1})
            return 200, '{"code":200,"message":"Success"}'

        with patch.object(adapter, "_request", side_effect=register_response):
            result = adapter.register("example.com")
        self.assertTrue(result.success, result.reason)

    def test_dynadot_registration_preserves_custom_domain_settings(self):
        for source in ("payload", "domain"):
            with self.subTest(source=source):
                domain = {"duration": 2, "privacy": "off", "registrant_contact_id": 123}
                config = {"payload": {"domain": domain, "currency": "EUR"}} if source == "payload" else {"domain": domain}
                original = json.dumps(config)
                adapter = DynadotRegistrar({"config": config}, "api-key:api-secret")
                with patch.object(adapter, "_request", return_value=(200, '{"code":200}')) as request:
                    self.assertTrue(adapter.register("example.com").success)
                self.assertEqual(request.call_args.args[2]["domain"], domain)
                if source == "payload":
                    self.assertEqual(request.call_args.args[2]["currency"], "EUR")
                self.assertEqual(json.dumps(config), original)

    def test_dynadot_registration_adds_defaults_without_mutating_config(self):
        for source in ("payload", "domain"):
            with self.subTest(source=source):
                domain = {"registrant_contact_id": 123}
                config = {"payload": {"domain": domain}} if source == "payload" else {"domain": domain}
                adapter = DynadotRegistrar({"config": config}, "api-key:api-secret")
                with patch.object(adapter, "_request", return_value=(200, '{"code":200}')) as request:
                    self.assertTrue(adapter.register("example.com").success)
                self.assertEqual(request.call_args.args[2]["domain"], {"registrant_contact_id": 123, "duration": 1, "privacy": "full"})
                self.assertEqual(domain, {"registrant_contact_id": 123})

    def test_dynadot_registration_rejects_non_object_domain_locally(self):
        for domain in (None, "example.com", []):
            with self.subTest(domain=domain):
                adapter = DynadotRegistrar({"config": {"payload": {"domain": domain}}}, "api-key:api-secret")
                with patch.object(adapter, "_request") as request:
                    with self.assertRaises(RegistrarError):
                        adapter.register("example.com")
                request.assert_not_called()

    def test_dynadot_test_accepts_success_with_http_200(self):
        adapter = DynadotRegistrar({}, "api-key:api-secret")
        with patch.object(adapter, "_request", return_value=(200, '{"code":200,"message":"Success","data":{}}') ):
            result = adapter.test()
        self.assertTrue(result[0])

    def test_dynadot_test_rejects_nested_json_error_with_http_200(self):
        adapter = DynadotRegistrar({}, "api-key:api-secret")
        response = '{"AccountInfoResponse":{"ResponseCode":"-1","Status":"error","Error":"invalid format"}}'
        with patch.object(adapter, "_request", return_value=(200, response)):
            result = adapter.test()
        self.assertFalse(result[0])

    def test_dynadot_request_uses_v2_auth_headers_and_path(self):
        adapter = DynadotRegistrar({}, "api-key:api-secret")
        request_id = uuid.UUID("123e4567-e89b-12d3-a456-426614174000")
        with (
            patch.object(registrar_adapters.uuid, "uuid4", return_value=request_id),
            patch.object(registrar_adapters, "_send_dynadot", return_value=(200, '{"code":200}')) as send,
        ):
            result = adapter.test()
        self.assertTrue(result[0])
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.dynadot.com/restful/v2/accounts/info")
        self.assertEqual(request.get_header("Authorization"), "Bearer api-key")
        self.assertTrue(request.get_header("X-request-id"))
        expected = base64.b64encode(
            hmac.new(
                b"api-secret",
                f"api-key\n/restful/v2/accounts/info\n{request_id}\n".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        self.assertEqual(request.get_header("X-signature"), expected)

    def test_dynadot_environment_uses_configured_endpoint_not_key_prefix(self):
        for endpoint in (DynadotRegistrar.endpoint, DynadotRegistrar.sandbox_endpoint):
            with self.subTest(endpoint=endpoint):
                adapter = DynadotRegistrar({"endpoint": endpoint}, "restricted_key:secret")
                with patch.object(registrar_adapters, "_send_dynadot", return_value=(200, '{"code":200}')) as send:
                    result = adapter.test()
                self.assertTrue(result[0])
                self.assertEqual(send.call_args.args[0].full_url, f"{endpoint}/restful/v2/accounts/info")

    def test_dynadot_wire_headers_and_body_match_signature(self):
        cases = [
            ("GET", "/restful/v2/accounts/info", None, None),
            ("POST", "/restful/v2/domains/example.test/register?currency=USD", {"note": "中文"}, '{"note":"中文"}'.encode()),
        ]
        for method, path, payload, expected_body in cases:
            with self.subTest(method=method), patch.object(registrar_adapters.http.client, "HTTPSConnection") as connect:
                connection = connect.return_value
                connection.getresponse.return_value.status = 200
                connection.getresponse.return_value.read.return_value = b'{"code":200}'
                result = DynadotRegistrar({}, "api-key:api-secret")._request(method, path, payload)
                self.assertEqual(result, (200, '{"code":200}'))
                connect.assert_called_once_with("api.dynadot.com", None, timeout=20)
                sent = connection.request.call_args
                self.assertEqual(sent.args, (method, path))
                self.assertEqual(sent.kwargs["body"], expected_body)
                headers = sent.kwargs["headers"]
                if expected_body is not None:
                    self.assertEqual(headers.get("Content-Type"), "application/json")
                request_id = headers["X-Request-ID"]
                self.assertEqual(str(uuid.UUID(request_id)), request_id)
                signed_bytes = f"api-key\n{path}\n{request_id}\n".encode() + (expected_body or b"")
                expected_signature = base64.b64encode(hmac.digest(b"api-secret", signed_bytes, "sha256")).decode()
                self.assertEqual(headers["X-Signature"], expected_signature)
                connection.close.assert_called_once()

    def test_dynadot_protocol_errors_close_connection_and_report_failure(self):
        with patch.object(registrar_adapters.http.client, "HTTPSConnection") as connect:
            connection = connect.return_value
            connection.getresponse.side_effect = registrar_adapters.http.client.BadStatusLine("invalid response")
            with self.assertRaises(RegistrarError):
                DynadotRegistrar({}, "api-key:api-secret").test()
            connection.close.assert_called_once()

    def test_generic_signature_header_keeps_existing_transport(self):
        request = Request("https://example.test/", headers={"X-Signature": "custom"})
        with patch.object(registrar_adapters, "urlopen") as send, patch.object(registrar_adapters.http.client, "HTTPSConnection") as connect:
            response = send.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{}'
            self.assertEqual(registrar_adapters._send(request), (200, '{}'))
            send.assert_called_once_with(request, timeout=20)
            connect.assert_not_called()

    def test_split_token_rejects_pasted_multiple_credential_pairs(self):
        adapter = DynadotRegistrar({}, "api-key:api-secret\nrestricted-key:restricted-secret")
        with self.assertRaisesRegex(RegistrarError, "不能包含换行"):
            adapter.test()

    def test_register_accepts_explicit_success_response(self):
        adapter = HttpJsonRegistrar({"endpoint": "https://example.test/register", "headers_json": "{}"}, "token")
        with patch.object(adapter, "_request", return_value=(200, '{"success": true}')) as request:
            result = adapter.register("a.com")
        self.assertTrue(result.success)
        request.assert_called_once_with("POST", {"domain": "a.com"})

    def test_register_rejects_success_false_response(self):
        adapter = HttpJsonRegistrar({"endpoint": "https://example.test/register", "headers_json": "{}"}, "")
        with patch.object(adapter, "_request", return_value=(200, '{"success": false}')):
            result = adapter.register("a.com")
        self.assertFalse(result.success)

    def test_platform_adapters_are_registered(self):
        self.assertIsInstance(create_adapter({"adapter": "aliyun_intl"}, "id:secret"), AliyunIntlRegistrar)
        self.assertIsInstance(create_adapter({"adapter": "godaddy"}, "key:secret"), GodaddyRegistrar)

    def test_availability_parser_handles_json_and_xml(self):
        self.assertTrue(_availability_result(200, '{"available": true}').available)
        self.assertFalse(_availability_result(200, "<Available>no</Available>").available)
        self.assertIsNone(_availability_result(200, '{"message":"accepted"}').available)


if __name__ == "__main__":
    unittest.main()
