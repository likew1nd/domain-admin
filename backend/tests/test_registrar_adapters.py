import base64
import hashlib
import hmac
import unittest
import uuid
from pathlib import Path
import sys
from unittest.mock import patch
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
    def test_dynadot_test_rejects_invalid_key_with_http_200(self):
        adapter = DynadotRegistrar({}, "api-key:api-secret")
        with patch.object(adapter, "_request", return_value=(200, '{"code":401,"message":"Unauthorized"}')):
            result = adapter.test()
        self.assertFalse(result[0])

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
