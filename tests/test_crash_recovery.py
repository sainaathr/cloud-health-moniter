import unittest
import json
import os
from unittest.mock import patch

from app.ui.app import app, app_state
import app.ui.app as app_module
import app.ui.incident_store as incident_store

class TestCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app_state["is_ready"] = True
        app_state["memory_leak"].clear()

    def test_crash_endpoint_disabled_by_default(self):
        """Destructive crash must be disabled by default (ENABLE_DESTRUCTIVE_CHAOS=False)."""
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', False):
            res = self.client.post('/api/chaos/crash')
            self.assertEqual(res.status_code, 403)
            data = json.loads(res.data)
            self.assertIn("disabled by server policy", data.get("error", ""))

    def test_crash_endpoint_rejects_query_param_bypass(self):
        """Query parameters or body confirm flags alone must never bypass authorization."""
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', True):
            # Passing ?confirm=true without server token must fail with 401
            res = self.client.post('/api/chaos/crash?confirm=true')
            self.assertEqual(res.status_code, 401)
            data = json.loads(res.data)
            self.assertIn("Genuine server-side authorization required", data.get("error", ""))

    def test_crash_endpoint_rejects_invalid_token(self):
        """Crash endpoint must reject invalid tokens."""
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', True):
            headers = {"X-Chaos-Token": "invalid-token-12345"}
            res = self.client.post('/api/chaos/crash', headers=headers)
            self.assertEqual(res.status_code, 401)

    def test_crash_endpoint_dry_run_authorized(self):
        """When authorized, dry-run mode validates safety gate without process exit."""
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', True):
            headers = {
                "X-Chaos-Token": app_module.CHAOS_AUTH_TOKEN,
                "X-Dry-Run": "true"
            }
            res = self.client.post('/api/chaos/crash', headers=headers)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.data)
            self.assertEqual(data.get("mode"), "dry_run")
            self.assertIn("Safety dry-run completed", data.get("message", ""))
            self.assertTrue(app_state["is_ready"])

    def test_simulated_crash_records_incident_and_recovers(self):
        """When authorized and in simulation mode, crash records incident and initiates healing."""
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', True):
            headers = {"X-Chaos-Token": app_module.CHAOS_AUTH_TOKEN}
            res = self.client.post('/api/chaos/crash', headers=headers)
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.data)
            self.assertEqual(data.get("status"), "success")
            self.assertFalse(app_state["is_ready"])

            # Verify incident recorded with SIMULATION category
            inc_id = data.get("incident_id")
            self.assertIsNotNone(inc_id)
            inc = incident_store.get_incident(inc_id)
            self.assertIsNotNone(inc)
            self.assertEqual(inc.get("category"), "SIMULATION")
            self.assertEqual(inc.get("status"), "SIMULATED_FAILURE")

            # Reset state for clean teardown
            app_state["is_ready"] = True

if __name__ == '__main__':
    unittest.main()
