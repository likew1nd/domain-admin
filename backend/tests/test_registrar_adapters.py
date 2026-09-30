import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.registrar_adapters import (
    AliyunIntlRegistrar,
    GodaddyRegistrar,
    HttpJsonRegistrar,
    _availability_result,
    create_adapter,
)


class RegistrarAdapterTests(unittest.TestCase):
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
