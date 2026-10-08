import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import monitor_service
from app.registrar_adapters import AvailabilityResult, RegistrarError


class MonitorServiceTests(unittest.TestCase):
    def setUp(self):
        self.manager = monitor_service.MonitorManager()
        self.adapter = Mock()
        self.enterContext(patch.object(monitor_service.db, "execute"))
        self.enterContext(patch.object(monitor_service, "decrypt_cookie", return_value="test-key:test-secret"))
        self.enterContext(patch.object(monitor_service, "create_adapter", return_value=self.adapter))
        self.lookup = self.enterContext(patch.object(monitor_service, "lookup"))
        self.save_state = self.enterContext(patch.object(self.manager, "_save_domain_state"))
        self.log = self.enterContext(patch.object(self.manager, "_log"))
        self.register = self.enterContext(patch.object(self.manager, "_register"))
        self.kick = self.enterContext(patch.object(self.manager, "_kick"))

    def check_domain(self):
        return self.manager._check_domain(
            "example.test",
            {"auto_register": True, "whois_retries": 2},
            [{"id": 1}],
            {"name": "DY", "token_ciphertext": ""},
            threading.Event(),
        )

    def test_http_429_keeps_domain_monitoring_without_whois_or_registration(self):
        self.adapter.check_available.return_value = AvailabilityResult(None, 429, "{}", "HTTP 429")

        self.assertEqual(self.check_domain(), "error")

        self.save_state.assert_called_once_with("example.test", "monitoring", "HTTP 429")
        self.lookup.assert_not_called()
        self.register.assert_not_called()
        self.kick.assert_not_called()

    def test_registrar_cooldown_keeps_domain_monitoring_and_explains_retry(self):
        reason = "Dynadot 请求限流，冷却中，剩余 10 秒"
        self.adapter.check_available.side_effect = RegistrarError(reason)

        self.assertEqual(self.check_domain(), "error")

        self.save_state.assert_called_once_with("example.test", "monitoring", reason)
        self.assertIn(reason, self.log.call_args.args[2])
        self.lookup.assert_not_called()
        self.register.assert_not_called()
        self.kick.assert_not_called()

    def test_explicit_unavailable_still_checks_whois_for_pending_delete(self):
        self.adapter.check_available.return_value = AvailabilityResult(False, 200, '{"available":false}')
        self.lookup.return_value = {"statuses": ["pendingDelete"]}

        self.assertEqual(self.check_domain(), "monitoring")

        self.lookup.assert_called_once_with("example.test")
        self.save_state.assert_called_once_with("example.test", "monitoring", "")
        self.log.assert_called_with("info", "whois", "当前状态：待删除", "example.test")
        self.register.assert_not_called()
        self.kick.assert_not_called()


if __name__ == "__main__":
    unittest.main()
