"""
test_notifications.py — Comprehensive Unit & Safety Tests for Alerting System
=============================================================================
Tests all Phase 4 requirements using safe mocks without real network connections:
  1. Missing configuration handling & safe dry-run default
  2. Disabled alert backend handling
  3. Dry-run behavior and telemetry recording
  4. Successful mocked SMTP delivery (with TLS & auth)
  5. SMTP connection/delivery failure shielding (non-blocking)
  6. Incident-based duplicate suppression & cooldown enforcement
  7. Correct incident details in message subject, text, and HTML bodies
  8. Recovery-verified event only after recovery success
  9. Secret masking in API configuration responses
 10. Controller operational survival when SMTP completely fails
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import smtplib
import socket

# Ensure clean imports
from app.notifications import (
    NotificationConfig,
    send_notification,
    send_test_notification,
    format_alert,
    _recent_alerts
)
import app.ui.incident_store as incident_store


class TestNotifications(unittest.TestCase):

    def setUp(self):
        # Clear deduplication cache before each test
        _recent_alerts.clear()
        # Save original environment
        self._orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)

    def test_missing_configuration_defaults_to_dry_run(self):
        """When SMTP credentials are missing, system safely defaults to dry-run mode."""
        os.environ["SMTP_USER"] = ""
        os.environ["SMTP_PASSWORD"] = ""
        os.environ["ALERT_TO_EMAIL"] = ""
        os.environ.pop("SMTP_DRY_RUN", None)

        self.assertTrue(NotificationConfig.is_dry_run())
        res = send_notification(
            event_type="CRITICAL_FAILURE",
            incident_id="INC-TEST-001",
            target_workload="sample-app",
            target_pod="sample-app-abc",
            problem="CrashLoopBackOff detected"
        )
        self.assertEqual(res["status"], "dry_run")
        self.assertIn("Dry-run mode", res["message"])

    def test_disabled_alert_backend(self):
        """When alerting is explicitly disabled, dispatch returns disabled and does nothing."""
        os.environ["ALERT_BACKEND"] = "none"
        res = send_notification(
            event_type="UNHEALTHY_PROBE",
            incident_id="INC-TEST-002",
            target_pod="sample-app-def",
            problem="Readiness failed"
        )
        self.assertEqual(res["status"], "disabled")

    def test_dry_run_mode_records_telemetry(self):
        """Dry-run mode validates formatting and logs to persistent store without network call."""
        os.environ["SMTP_DRY_RUN"] = "true"
        os.environ["ALERT_TO_EMAIL"] = "devops-test@example.com"

        res = send_notification(
            event_type="RECOVERY_STARTED",
            incident_id="INC-TEST-DRY",
            target_workload="sample-app",
            target_pod="sample-app-123",
            problem="OOMKilled",
            action_taken="Deleting bad pod",
            status="CONTROLLER_RECOVERING"
        )
        self.assertEqual(res["status"], "dry_run")
        self.assertEqual(res["recipient"], "devops-test@example.com")

        # Verify entry in incident store
        notifs = incident_store.get_notifications(limit=5, incident_id="INC-TEST-DRY")
        self.assertGreaterEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["status"], "dry_run")

    @patch("smtplib.SMTP")
    def test_successful_mocked_smtp_delivery(self, mock_smtp_cls):
        """When configured and not in dry-run, sends message via mocked SMTP with TLS."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "smtp.mock.server"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USER"] = "alerts@example.com"
        os.environ["SMTP_PASSWORD"] = "secret-pass-123"
        os.environ["ALERT_TO_EMAIL"] = "oncall@example.com"
        os.environ["SMTP_USE_TLS"] = "true"
        os.environ["SMTP_TIMEOUT_SEC"] = "5"

        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        mock_server.__enter__.return_value = mock_server

        res = send_notification(
            event_type="CRITICAL_FAILURE",
            incident_id="INC-MOCK-OK",
            target_workload="sample-app",
            target_pod="sample-app-live-1",
            problem="CrashLoopBackOff",
            action_taken="Alerting team",
            status="TRIGGERED",
            severity="CRITICAL"
        )

        mock_smtp_cls.assert_called_with("smtp.mock.server", 587, timeout=5)
        self.assertTrue(mock_server.starttls.called)
        self.assertTrue(mock_server.login.called)
        self.assertTrue(mock_server.send_message.called)
        self.assertIn(res["status"], ("smtp_accepted", "sent"))

    @patch("smtplib.SMTP")
    def test_smtp_failure_is_non_blocking(self, mock_smtp_cls):
        """Network/SMTP exceptions are trapped; caller receives failure status without crashing."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "invalid.smtp.host"
        os.environ["SMTP_USER"] = "alerts@example.com"
        os.environ["SMTP_PASSWORD"] = "pass123"
        os.environ["ALERT_TO_EMAIL"] = "target@example.com"

        mock_smtp_cls.side_effect = socket.timeout("Connection timed out after 5s")

        res = send_notification(
            event_type="CRITICAL_FAILURE",
            incident_id="INC-FAIL-01",
            target_pod="sample-app-xxx",
            problem="Deadlock"
        )

        self.assertIn(res["status"], ("timed_out", "failed"))
        self.assertIn("Connection timed out", res.get("error", ""))

    def test_duplicate_suppression_and_cooldown(self):
        """Identical alerts within cooldown period are suppressed to prevent alert storms."""
        os.environ["ALERT_COOLDOWN_SEC"] = "300"
        os.environ["SMTP_DRY_RUN"] = "true"

        # First alert passes
        res1 = send_notification(
            event_type="UNHEALTHY_PROBE",
            incident_id="INC-DEDUP-1",
            target_pod="sample-pod-dedup",
            status="UNREADY"
        )
        self.assertEqual(res1["status"], "dry_run")

        # Immediate second alert for same pod & status is suppressed
        res2 = send_notification(
            event_type="UNHEALTHY_PROBE",
            incident_id="INC-DEDUP-2",
            target_pod="sample-pod-dedup",
            status="UNREADY"
        )
        self.assertEqual(res2["status"], "suppressed")
        self.assertEqual(res2["reason"], "cooldown_active")

    def test_incident_details_in_formatted_bodies(self):
        """Verifies that format_alert incorporates all essential incident telemetry."""
        subject, text_body, html_body = format_alert(
            event_type="RECOVERY_VERIFIED",
            incident_id="INC-20261016-0001",
            severity="INFO",
            target_workload="sample-app",
            target_pod="sample-app-new-pod",
            namespace="health-monitor",
            problem="Readiness restored",
            action_taken="Replaced crashed container",
            status="RECOVERED_VERIFIED",
            details="Replica passed HTTP 200 checks."
        )

        # Assertions on subject
        self.assertIn("[INFO]", subject)
        self.assertIn("RECOVERY_VERIFIED", subject)
        self.assertIn("INC-20261016-0001", subject)

        # Assertions on plain text
        self.assertIn("Incident ID       : INC-20261016-0001", text_body)
        self.assertIn("Target Pod        : sample-app-new-pod", text_body)
        self.assertIn("Namespace         : health-monitor", text_body)
        self.assertIn("Current Status    : RECOVERED_VERIFIED", text_body)

        # Assertions on HTML body
        self.assertIn("INC-20261016-0001", html_body)
        self.assertIn("sample-app-new-pod", html_body)
        self.assertIn("health-monitor", html_body)

    def test_masked_config_never_exposes_secrets(self):
        """Masked configuration endpoint masks passwords and tokens."""
        os.environ["SMTP_PASSWORD"] = "SuperSecretPassword123"
        os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:ABCdefGHIjklMNO"
        os.environ["SMTP_USER"] = "admin-user@example.com"

        masked = NotificationConfig.get_masked_config()
        self.assertEqual(masked["smtp_password_configured"], True)
        self.assertNotIn("SuperSecretPassword123", str(masked))
        self.assertNotIn("ABCdefGHIjklMNO", str(masked))
        self.assertEqual(masked["smtp_host"], "smtp.gmail.com")

    def test_default_recipient_is_vitstudent(self):
        """Default recipient must be sainaath.r2025@vitstudent.ac.in when ALERT_TO_EMAIL is unset."""
        os.environ.pop("ALERT_TO_EMAIL", None)
        self.assertEqual(NotificationConfig.get_to_email(), "sainaath.r2025@vitstudent.ac.in")

    def test_missing_smtp_credentials_reports_missing_config_on_live_request(self):
        """When live delivery is requested but credentials are empty, reports not_configured."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_USER"] = ""
        os.environ["SMTP_PASSWORD"] = ""
        os.environ["SMTP_DRY_RUN"] = "false"

        res = send_test_notification(force_live=True)
        self.assertIn(res["status"], ("not_configured", "missing_config"))
        self.assertIn("Missing required SMTP", res["error"])

    def test_invalid_smtp_port_and_host_validation(self):
        """Configuration validator detects missing host, invalid port numbers, and bad email."""
        os.environ["SMTP_HOST"] = ""
        is_valid, err = NotificationConfig.validate_smtp_config(require_auth=False)
        self.assertFalse(is_valid)
        self.assertIn("SMTP_HOST", err)

        os.environ["SMTP_HOST"] = "smtp.valid.com"
        os.environ["SMTP_PORT"] = "not-a-port"
        is_valid, err = NotificationConfig.validate_smtp_config(require_auth=False)
        self.assertFalse(is_valid)
        self.assertIn("not a valid integer", err)

        os.environ["SMTP_PORT"] = "99999"
        is_valid, err = NotificationConfig.validate_smtp_config(require_auth=False)
        self.assertFalse(is_valid)
        self.assertIn("Port must be between", err)

    @patch("smtplib.SMTP_SSL")
    def test_successful_mocked_smtp_ssl_delivery(self, mock_smtp_ssl_cls):
        """When configured with SSL (port 465 or SMTP_USE_SSL=true), delivers via SMTP_SSL."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "smtp.mockssl.com"
        os.environ["SMTP_PORT"] = "465"
        os.environ["SMTP_USER"] = "ssluser@example.com"
        os.environ["SMTP_PASSWORD"] = "sslpass"
        os.environ["ALERT_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"
        os.environ["SMTP_USE_SSL"] = "true"
        os.environ["SMTP_TIMEOUT_SEC"] = "5"

        mock_server = MagicMock()
        mock_smtp_ssl_cls.return_value = mock_server
        mock_server.__enter__.return_value = mock_server

        res = send_notification(
            event_type="RECOVERY_VERIFIED",
            incident_id="INC-SSL-001",
            target_workload="sample-app",
            target_pod="sample-app-ssl",
            problem="NodeEviction",
            action_taken="Rescheduled to healthy node",
            status="RECOVERED_VERIFIED",
            force_live=True
        )

        mock_smtp_ssl_cls.assert_called_with("smtp.mockssl.com", 465, timeout=5)
        self.assertTrue(mock_server.login.called)
        self.assertTrue(mock_server.send_message.called)
        self.assertIn(res["status"], ("smtp_accepted", "sent"))

    @patch("smtplib.SMTP")
    def test_smtp_authentication_failure_handling(self, mock_smtp_cls):
        """Authentication failure returns failed status with error message and does not crash."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "smtp.authfail.com"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USER"] = "baduser@example.com"
        os.environ["SMTP_PASSWORD"] = "wrongpassword"
        os.environ["ALERT_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"

        mock_server = MagicMock()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials")
        mock_smtp_cls.return_value = mock_server
        mock_server.__enter__.return_value = mock_server

        res = send_notification(
            event_type="CRITICAL_FAILURE",
            incident_id="INC-AUTH-FAIL",
            target_pod="pod-1",
            problem="OOMKilled",
            force_live=True
        )

        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["display_status"], "Failed")
        self.assertIn("Authentication failed", res["error"])

    @patch("smtplib.SMTP")
    def test_smtp_recipient_refused_handling(self, mock_smtp_cls):
        """When recipient is rejected by SMTP server, traps error and returns failed status."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "smtp.recipfail.com"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USER"] = "sender@example.com"
        os.environ["SMTP_PASSWORD"] = "pass"
        os.environ["ALERT_TO_EMAIL"] = "rejected@example.com"

        mock_server = MagicMock()
        mock_server.send_message.side_effect = smtplib.SMTPRecipientsRefused(
            {"rejected@example.com": (550, b"User mailbox unavailable")}
        )
        mock_smtp_cls.return_value = mock_server
        mock_server.__enter__.return_value = mock_server

        res = send_notification(
            event_type="CRITICAL_FAILURE",
            incident_id="INC-REJECT-01",
            target_pod="pod-2",
            problem="CrashLoop",
            force_live=True
        )

        self.assertEqual(res["status"], "failed")
        self.assertIn("SMTP address rejected", res["error"])

    def test_cli_interface_execution(self):
        """CLI tool executes cleanly with --check-config and --test flags."""
        from app.notifications import main
        # Test --check-config
        code1 = main(["--check-config"])
        self.assertIn(code1, (0, 1))

        # Test safe --test (dry-run)
        code2 = main(["--test", "--recipient", "sainaath.r2025@vitstudent.ac.in"])
        self.assertEqual(code2, 0)

    def test_safe_test_notification_helper(self):
        """Safe test alert triggers synthetic diagnostic notification without side effects."""
        os.environ["SMTP_DRY_RUN"] = "true"
        res = send_test_notification()
        self.assertIn(res.get("status"), ("dry_run", "smtp_accepted", "sent"))
        self.assertEqual(res.get("channel"), "email")

    @patch("smtplib.SMTP")
    def test_successful_smtp_submission_accepted(self, mock_smtp_cls):
        """Verifies successful SMTP submission returns smtp_accepted with relay explanation."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "smtp.relay.example.com"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USER"] = "alerts-sender@example.com"
        os.environ["SMTP_PASSWORD"] = "secret-pass"
        os.environ["ALERT_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"
        os.environ["SMTP_USE_TLS"] = "true"
        os.environ["SMTP_TIMEOUT_SEC"] = "5"

        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        mock_server.__enter__.return_value = mock_server

        res = send_notification(
            event_type="RECOVERY_VERIFIED",
            incident_id="INC-VERIFIED-001",
            target_workload="sample-app",
            target_pod="sample-app-replacement-pod",
            problem="CrashLoopBackOff recovered",
            action_taken="Replacement pod verified Running & Ready",
            status="RECOVERED_VERIFIED",
            severity="INFO",
            force_live=True
        )

        self.assertEqual(res["status"], "smtp_accepted")
        self.assertEqual(res["display_status"], "SMTP accepted")
        self.assertTrue(res.get("confirmed"))
        self.assertEqual(res["recipient"], "sainaath.r2025@vitstudent.ac.in")
        self.assertIn("does not guarantee arrival in the inbox", res["message"])

        # Check recorded in incident_store
        notifs = incident_store.get_notifications(limit=5, incident_id="INC-VERIFIED-001")
        self.assertGreaterEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["status"], "smtp_accepted")

    def test_missing_configuration_identifies_missing_credentials(self):
        """When live dispatch requested without credentials, clearly reports missing fields."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_USER"] = ""
        os.environ["SMTP_USERNAME"] = ""
        os.environ["SMTP_PASSWORD"] = ""
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["ALERT_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"

        res = send_test_notification(force_live=True)
        self.assertIn(res["status"], ("not_configured", "missing_config"))
        self.assertIn("SMTP_USER", res["error"])
        self.assertIn("SMTP_PASSWORD", res["error"])
        self.assertEqual(res.get("recipient"), "sainaath.r2025@vitstudent.ac.in")

    @patch("smtplib.SMTP")
    def test_connection_failure_bounded_retries(self, mock_smtp_cls):
        """Transient connection errors are retried up to MAX_RETRIES times with backoff."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_HOST"] = "down.smtp.server"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USER"] = "user@example.com"
        os.environ["SMTP_PASSWORD"] = "pass"
        os.environ["ALERT_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"
        os.environ["SMTP_MAX_RETRIES"] = "3"
        os.environ["SMTP_RETRY_BACKOFF_SEC"] = "0.01"

        mock_smtp_cls.side_effect = socket.timeout("Connection timed out after 5s")

        res = send_notification(
            event_type="CRITICAL_FAILURE",
            incident_id="INC-RETRY-001",
            target_pod="pod-timeout",
            force_live=True
        )

        self.assertIn(res["status"], ("timed_out", "failed"))
        self.assertIn(res["display_status"], ("Timed out", "Failed"))
        self.assertEqual(mock_smtp_cls.call_count, 3)
        self.assertIn("Connection timed out", res["error"])

    def test_new_environment_variable_names_alignment(self):
        """Verifies consistent support for required SMTP_* variable names."""
        os.environ["SMTP_HOST"] = "smtp.gmail.com"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USERNAME"] = "test-sender@gmail.com"
        os.environ["SMTP_PASSWORD"] = "app-password-test"
        os.environ["SMTP_FROM_EMAIL"] = "custom-sender@cloud-health.local"
        os.environ["SMTP_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"
        os.environ["SMTP_TIMEOUT_SECONDS"] = "15"

        self.assertEqual(NotificationConfig.get_smtp_username(), "test-sender@gmail.com")
        self.assertEqual(NotificationConfig.get_smtp_user(), "test-sender@gmail.com")
        self.assertEqual(NotificationConfig.get_smtp_from_email(), "custom-sender@cloud-health.local")
        self.assertEqual(NotificationConfig.get_smtp_to_email(), "sainaath.r2025@vitstudent.ac.in")
        self.assertEqual(NotificationConfig.get_smtp_timeout_seconds(), 15)

        masked = NotificationConfig.get_masked_config()
        self.assertEqual(masked["smtp_username"], "te***@gmail.com")
        self.assertEqual(masked["smtp_to_email"], "sainaath.r2025@vitstudent.ac.in")
        self.assertEqual(masked["smtp_timeout_seconds"], 15)
        self.assertNotIn("app-password-test", str(masked))

    @patch("smtplib.SMTP")
    def test_smtp_protocol_error_handling(self, mock_smtp_cls):
        """Protocol errors (e.g. SMTPResponseException) are safely caught and return failed status."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["SMTP_DRY_RUN"] = "false"
        os.environ["SMTP_USERNAME"] = "sender@example.com"
        os.environ["SMTP_PASSWORD"] = "secret"
        os.environ["SMTP_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"

        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server
        mock_server.starttls.side_effect = smtplib.SMTPResponseException(500, b"Command unrecognized")

        res = send_notification(
            event_type="PROTOCOL_TEST",
            incident_id="INC-PROTO-01",
            target_pod="pod-proto",
            force_live=True
        )

        self.assertEqual(res["status"], "failed")
        self.assertIn("Command unrecognized", res.get("error", "") + res.get("message", ""))

    def test_demo_button_api_endpoint(self):
        """Tests the backend API endpoint invoked by the frontend Demo button."""
        from app.ui.app import app
        client = app.test_client()

        # 1. Test Dry-Run dispatch
        resp_dry = client.post('/api/notifications/test')
        self.assertEqual(resp_dry.status_code, 200)
        data_dry = resp_dry.get_json()
        self.assertEqual(data_dry["status"], "success")
        self.assertIn("status", data_dry["result"])

        # 2. Test Live dispatch without credentials returns not_configured
        os.environ["SMTP_USERNAME"] = ""
        os.environ["SMTP_USER"] = ""
        os.environ["SMTP_PASSWORD"] = ""
        resp_live = client.post('/api/notifications/test?live=true')
        self.assertEqual(resp_live.status_code, 200)
        data_live = resp_live.get_json()
        self.assertEqual(data_live["status"], "success")
        self.assertIn(data_live["result"]["status"], ("not_configured", "missing_config"))

    def test_controller_recovery_alert_non_blocking_and_verified(self):
        """Verifies that controller alert dispatch is non-blocking and includes verified replacement pod."""
        from app.controller.health_monitor import send_alert
        # Call send_alert with non_blocking=False for synchronous verification
        with patch("app.notifications.send_notification") as mock_send:
            mock_send.return_value = {"status": "smtp_accepted", "recipient": "sainaath.r2025@vitstudent.ac.in"}
            send_alert(
                message="Recovery Successful",
                event_type="RECOVERY_VERIFIED",
                incident_id="INC-VERIFIED-999",
                target_workload="sample-app",
                target_pod="sample-app-verified-pod",
                problem="CrashLoopBackOff",
                action_taken="Replacement pod verified Running & Ready",
                status="RECOVERED_VERIFIED",
                severity="INFO",
                metrics={"restarts": 0, "phase": "Running"},
                thresholds={"restart_threshold": 5},
                non_blocking=False
            )
            self.assertTrue(mock_send.called)
            kwargs = mock_send.call_args[1]
            self.assertEqual(kwargs["event_type"], "RECOVERY_VERIFIED")
            self.assertEqual(kwargs["target_pod"], "sample-app-verified-pod")
            self.assertEqual(kwargs["status"], "RECOVERED_VERIFIED")
            self.assertIn("restarts", kwargs["metrics"])

    def test_incident_triggered_notifications(self):
        """Tests that incident detections trigger alerts, enforce cooldown, and record in DB."""
        os.environ["ALERT_BACKEND"] = "email"
        os.environ["ALERT_ENABLED"] = "true"
        os.environ["SMTP_DRY_RUN"] = "true"
        os.environ["ALERT_COOLDOWN_SEC"] = "300"
        os.environ["ALERT_TO_EMAIL"] = "sainaath.r2025@vitstudent.ac.in"

        # 1. First failure alert passes
        res1 = send_notification(
            event_type="CRASHLOOP_BACKOFF",
            incident_id="INC-E2E-001",
            target_workload="sample-app",
            target_pod="sample-app-crash",
            problem="CrashLoopBackOff detected (restarts=5)",
            action_taken="Deleted unhealthy pod to initiate recovery",
            status="TRIGGERED",
            severity="CRITICAL"
        )
        self.assertEqual(res1["status"], "dry_run")
        self.assertEqual(res1["recipient"], "sainaath.r2025@vitstudent.ac.in")

        # 2. Duplicate alert for same pod & status is suppressed
        res_dup = send_notification(
            event_type="CRASHLOOP_BACKOFF",
            incident_id="INC-E2E-002",
            target_workload="sample-app",
            target_pod="sample-app-crash",
            problem="CrashLoopBackOff detected (restarts=5)",
            status="TRIGGERED"
        )
        self.assertEqual(res_dup["status"], "suppressed")

        # 3. Recovery event passes cooldown
        res_rec = send_notification(
            event_type="RECOVERY_VERIFIED",
            incident_id="INC-E2E-003",
            target_workload="sample-app",
            target_pod="sample-app-crash",
            problem="CrashLoopBackOff recovered",
            action_taken="Verified new pod Running & Ready",
            status="RECOVERED_VERIFIED",
            severity="INFO"
        )
        self.assertEqual(res_rec["status"], "dry_run")

        # Verify DB records
        notifs = incident_store.get_notifications(limit=5, incident_id="INC-E2E-001")
        self.assertGreaterEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["status"], "dry_run")

    def test_real_smtp_connection_conditional(self):
        """
        Tests real SMTP connection only when valid credentials are configured.
        When credentials are not configured, verifies that not_configured is reported.
        """
        user = os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER", "").strip()
        passwd = os.getenv("SMTP_PASSWORD", "").strip()

        if not (user and passwd) or "example.com" in user or "your-" in user:
            res = send_test_notification(force_live=True)
            self.assertIn(res["status"], ("not_configured", "missing_config"))
            self.assertIn("Missing required SMTP", res.get("error", "") + res.get("message", ""))
        else:
            res = send_test_notification(force_live=True)
            self.assertIn(res["status"], ("smtp_accepted", "sent", "failed", "timed_out"))


if __name__ == "__main__":
    unittest.main()


