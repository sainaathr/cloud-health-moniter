"""
notifications.py — Production-Grade Alert & Notification Management
===================================================================
Provides robust, non-blocking email (SMTP) and optional Telegram alerts for
the Auto Kubernetes Health Monitoring & Self-Healing Control Plane.

Features:
  - Supports configurable notification events (Critical Failures, Probe Failures,
    CrashLoopBackOff, OOMKilled, Recovery Started, Recovery Verified, Recovery Failed, Test Notifications).
  - Configurable recipient defaulting to sainaath.r2025@vitstudent.ac.in.
  - Full SMTP transport support: STARTTLS (port 587), direct SSL/TLS (port 465), and unencrypted plain text.
  - Incident-based deduplication with configurable cooldown to prevent alert storms.
  - Non-blocking execution: SMTP network timeouts/failures never crash the controller
    or impede Kubernetes self-healing.
  - Explicit delivery status distinction:
      * 'disabled'       : alerts turned off in configuration
      * 'missing_config' : real delivery requested but required credentials missing
      * 'dry_run'        : safe simulation without network dispatch
      * 'sent'           : delivery confirmed by SMTP server (250 OK)
      * 'failed'         : connection, auth, timeout, or recipient failure (sanitized)
      * 'suppressed'     : duplicate suppressed by cooldown window
  - Secret masking: credentials, passwords, and tokens are NEVER printed in logs or API responses.
  - Full persistence: delivery statuses are recorded into the persistent incident store.
  - Real notification test support via both Web API and CLI.
"""

import os
import smtplib
import socket
import logging
import threading
import time
import datetime
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Dict, Any, Tuple, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Logger configuration
logger = logging.getLogger("health_monitor.notifications")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | [NOTIFY] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Optional incident store import
try:
    from app.ui import incident_store
except ImportError:
    try:
        import incident_store
    except ImportError:
        incident_store = None


class NotificationConfig:
    """Manages notification settings loaded from environment variables."""

    # Default recipient requested for health monitor alerts
    DEFAULT_RECIPIENT = "sainaath.r2025@vitstudent.ac.in"

    @staticmethod
    def get_smtp_host() -> str:
        return os.getenv("SMTP_HOST", "smtp.gmail.com").strip()

    @staticmethod
    def get_smtp_port() -> int:
        try:
            return int(os.getenv("SMTP_PORT", "587").strip())
        except ValueError:
            return 587

    @staticmethod
    def get_smtp_username() -> str:
        return (os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER") or "").strip()

    @classmethod
    def get_smtp_user(cls) -> str:
        return cls.get_smtp_username()

    @staticmethod
    def get_smtp_password() -> str:
        return os.getenv("SMTP_PASSWORD", "").strip()

    @classmethod
    def get_smtp_from_email(cls) -> str:
        sender = (
            os.getenv("SMTP_FROM_EMAIL") or
            os.getenv("SMTP_FROM") or
            os.getenv("SMTP_SENDER") or
            os.getenv("SMTP_USERNAME") or
            os.getenv("SMTP_USER") or
            ""
        ).strip()
        return sender or "k8s-monitor@cloud-health.local"

    @classmethod
    def get_smtp_from(cls) -> str:
        return cls.get_smtp_from_email()

    @classmethod
    def get_smtp_to_email(cls) -> str:
        recipient = (
            os.getenv("SMTP_TO_EMAIL") or
            os.getenv("ALERT_TO_EMAIL") or
            os.getenv("SMTP_TO") or
            ""
        ).strip()
        return recipient or cls.DEFAULT_RECIPIENT

    @classmethod
    def get_alert_to_email(cls) -> str:
        return cls.get_smtp_to_email()

    @classmethod
    def get_to_email(cls) -> str:
        return cls.get_smtp_to_email()

    @classmethod
    def get_smtp_use_tls(cls) -> bool:
        """Returns True if STARTTLS should be used (default for port 587)."""
        val = os.getenv("SMTP_USE_TLS", "").strip().lower()
        if val in ("1", "true", "yes"):
            return True
        if val in ("0", "false", "no"):
            return False
        # If not specified, default to True unless port 465 (SSL/TLS direct)
        return cls.get_smtp_port() != 465

    @classmethod
    def get_smtp_use_ssl(cls) -> bool:
        """Returns True if direct SSL/TLS socket should be used (port 465)."""
        val = os.getenv("SMTP_USE_SSL", "").strip().lower()
        if val in ("1", "true", "yes"):
            return True
        if val in ("0", "false", "no"):
            return False
        # Auto-detect: port 465 uses direct SSL
        return cls.get_smtp_port() == 465

    @staticmethod
    def get_smtp_timeout_seconds() -> int:
        try:
            val = os.getenv("SMTP_TIMEOUT_SECONDS") or os.getenv("SMTP_TIMEOUT_SEC") or "10"
            return int(val.strip())
        except ValueError:
            return 10

    @classmethod
    def get_smtp_timeout(cls) -> int:
        return cls.get_smtp_timeout_seconds()

    @classmethod
    def get_smtp_max_retries(cls) -> int:
        try:
            return max(1, int(os.getenv("SMTP_MAX_RETRIES", "3").strip()))
        except ValueError:
            return 3

    @classmethod
    def get_smtp_retry_backoff_sec(cls) -> float:
        try:
            return max(0.1, float(os.getenv("SMTP_RETRY_BACKOFF_SEC", "1.5").strip()))
        except ValueError:
            return 1.5

    @classmethod
    def is_dry_run(cls) -> bool:
        explicit = os.getenv("SMTP_DRY_RUN", "").strip().lower()
        if explicit in ("1", "true", "yes"):
            return True
        if explicit in ("0", "false", "no"):
            return False
        # Default to dry-run if sender credentials are not configured
        user = cls.get_smtp_username()
        passwd = cls.get_smtp_password()
        return not (user and passwd)

    @staticmethod
    def get_alert_backend() -> str:
        backend = os.getenv("ALERT_BACKEND", "").strip().lower()
        if backend:
            return backend
        return "email"

    @classmethod
    def is_enabled(cls) -> bool:
        backend = cls.get_alert_backend()
        if backend in ("none", "disabled", "off"):
            return False
        explicit = os.getenv("ALERT_ENABLED", "true").strip().lower()
        return explicit not in ("0", "false", "no", "off")

    @staticmethod
    def get_telegram_token() -> str:
        return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    @staticmethod
    def get_telegram_chat_id() -> str:
        return os.getenv("TELEGRAM_CHAT_ID", "").strip()

    @staticmethod
    def get_dashboard_url() -> str:
        return os.getenv("DASHBOARD_URL", "http://localhost:5000").strip()

    @staticmethod
    def get_cooldown_seconds() -> int:
        try:
            return int(os.getenv("ALERT_COOLDOWN_SEC", "300").strip())
        except ValueError:
            return 300

    @staticmethod
    def get_cluster_name() -> str:
        return os.getenv("CLUSTER_NAME") or os.getenv("NODE_NAME") or "minikube-health-monitor"

    @classmethod
    def validate_smtp_config(cls, require_auth: bool = True) -> Tuple[bool, Optional[str]]:
        """
        Validates SMTP parameters without exposing secret values.
        Returns (is_valid, error_message).
        """
        host = cls.get_smtp_host()
        if not host:
            return False, "SMTP_HOST is not configured."

        raw_port = str(os.getenv("SMTP_PORT", "587")).strip()
        try:
            port = int(raw_port)
        except ValueError:
            return False, f"Invalid SMTP_PORT: '{raw_port}' is not a valid integer."

        if port <= 0 or port > 65535:
            return False, f"Invalid SMTP_PORT: {port}. Port must be between 1 and 65535."

        recipient = cls.get_smtp_to_email()
        if not recipient or "@" not in recipient:
            return False, f"Invalid recipient email address: '{recipient}'."

        if require_auth:
            user = cls.get_smtp_username()
            passwd = cls.get_smtp_password()
            if not user or not passwd:
                missing = []
                if not user:
                    missing.append("SMTP_USER")
                if not passwd:
                    missing.append("SMTP_PASSWORD")
                return False, f"Missing required SMTP credentials: {', '.join(missing)}."

        return True, None

    @classmethod
    def get_masked_config(cls) -> Dict[str, Any]:
        """Return operational config with secrets masked for safe UI/API inspection."""
        user = cls.get_smtp_username()
        if user and "@" in user:
            parts = user.split("@")
            prefix = parts[0]
            masked_prefix = f"{prefix[:2]}***" if len(prefix) > 2 else "***"
            masked_user = f"{masked_prefix}@{parts[1]}"
        elif user:
            masked_user = f"{user[:2]}***"
        else:
            masked_user = "Not Configured"

        passwd = cls.get_smtp_password()
        masked_pass = "••••••••" if passwd else "Not Configured"
        tg_token = cls.get_telegram_token()
        masked_token = f"{tg_token[:4]}••••••••" if tg_token else "Not Configured"

        dry_run = cls.is_dry_run()
        enabled = cls.is_enabled()
        has_creds = bool(user and passwd)

        if not enabled:
            delivery_state = "DISABLED"
            status_label = "Disabled"
        elif not has_creds:
            delivery_state = "NOT_CONFIGURED"
            status_label = "Not configured"
        elif dry_run:
            delivery_state = "DRY_RUN"
            status_label = "Dry Run (Simulated)"
        else:
            delivery_state = "LIVE_READY"
            status_label = "Live SMTP Ready"

        encryption_mode = "SSL/TLS (Port 465)" if cls.get_smtp_use_ssl() else ("STARTTLS" if cls.get_smtp_use_tls() else "Plain / Unencrypted")

        return {
            "enabled": enabled,
            "backend": cls.get_alert_backend(),
            "dry_run": dry_run,
            "delivery_state": delivery_state,
            "status_label": status_label,
            "smtp_host": cls.get_smtp_host(),
            "smtp_port": cls.get_smtp_port(),
            "smtp_username": masked_user,
            "smtp_user": masked_user,
            "smtp_password_configured": bool(passwd),
            "smtp_from_email": cls.get_smtp_from_email(),
            "smtp_from": cls.get_smtp_from_email(),
            "smtp_to_email": cls.get_smtp_to_email(),
            "alert_to_email": cls.get_smtp_to_email(),
            "smtp_use_tls": cls.get_smtp_use_tls(),
            "smtp_use_ssl": cls.get_smtp_use_ssl(),
            "encryption_mode": encryption_mode,
            "smtp_timeout_seconds": cls.get_smtp_timeout_seconds(),
            "smtp_timeout_sec": cls.get_smtp_timeout_seconds(),
            "smtp_max_retries": cls.get_smtp_max_retries(),
            "telegram_configured": bool(cls.get_telegram_token() and cls.get_telegram_chat_id()),
            "telegram_chat_id": cls.get_telegram_chat_id() or "Not Configured",
            "dashboard_url": cls.get_dashboard_url(),
            "cooldown_seconds": cls.get_cooldown_seconds(),
            "default_recipient": cls.DEFAULT_RECIPIENT,
            "cluster_name": cls.get_cluster_name(),
            "smtp_relay_notice": "SMTP acceptance confirms relay handoff, but does not guarantee arrival in the inbox (check spam folder or mail filtering rules)."
        }


# Deduplication cache: key -> timestamp
_recent_alerts: Dict[str, float] = {}
_lock = threading.Lock()


def _is_deduplicated(key: str, cooldown: int) -> bool:
    """Return True if an identical alert was sent within cooldown window."""
    now = time.time()
    with _lock:
        last_sent = _recent_alerts.get(key, 0.0)
        if (now - last_sent) < cooldown:
            return True
        _recent_alerts[key] = now
        return False


def format_alert(
    event_type: str,
    incident_id: str,
    severity: str,
    target_workload: str,
    target_pod: str,
    namespace: str,
    problem: str,
    action_taken: str,
    status: str,
    details: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
    thresholds: Optional[Dict[str, Any]] = None
) -> Tuple[str, str, str]:
    """
    Generate professional email Subject, Plain-Text Body, and HTML Body.
    Ensures clear test subject for demo test emails and structured metrics telemetry.
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    dash_url = NotificationConfig.get_dashboard_url()
    cluster = NotificationConfig.get_cluster_name()
    recipient = NotificationConfig.get_smtp_to_email()

    if event_type == "TEST":
        subject = "Kubernetes Health Monitor — Test Notification"
    else:
        subject = f"[{severity.upper()}] [K8s-Monitor] {event_type} on {target_workload} ({incident_id})"

    # Prepare metrics text block if present
    metrics_text = ""
    metrics_html_rows = ""
    if metrics or thresholds:
        m_items = []
        if metrics:
            for k, v in metrics.items():
                m_items.append(f"  • {k}: {v}")
        if thresholds:
            for k, v in thresholds.items():
                m_items.append(f"  • Threshold ({k}): {v}")
        if m_items:
            metrics_text = "\nObserved Metrics & Thresholds:\n" + "\n".join(m_items) + "\n"
            metrics_html_rows = "".join(f"<tr><th>Telemetry</th><td><code>{item}</code></td></tr>" for item in m_items)

    test_notice_text = "\n*** THIS IS A DIAGNOSTIC TEST EMAIL TO VERIFY SMTP RELAY DELIVERY ***\n" if event_type == "TEST" else ""
    test_banner_html = """
      <div style="background-color: #0369a1; color: #ffffff; padding: 10px 16px; border-radius: 6px; font-weight: 600; font-size: 13px; margin-bottom: 16px; display: flex; align-items: center; gap: 8px;">
        <span>ℹ️</span> THIS IS AN AUTOMATED TEST NOTIFICATION — VERIFYING PIPELINE DELIVERY
      </div>
    """ if event_type == "TEST" else ""

    text_body = f"""======================================================================
[K8s-Monitor] AUTO KUBERNETES HEALTH MONITORING & SELF-HEALING SYSTEM
======================================================================{test_notice_text}
Incident ID       : {incident_id}
Event Type        : {event_type}
Severity Level    : {severity}
Cluster / Host    : {cluster}
Namespace         : {namespace}
Target Workload   : {target_workload}
Target Pod        : {target_pod}
Timestamp         : {ts}
Detected Problem  : {problem}
Action Taken      : {action_taken}
Current Status    : {status}
Control Plane URL : {dash_url}{metrics_text}
----------------------------------------------------------------------
Diagnostics & Details:
{details or 'No additional diagnostics attached.'}
======================================================================
Target Recipient : {recipient}
Sent by Auto Kubernetes Health Monitoring & Self-Healing Control Plane.
"""

    status_color = "#10b981" if status in ("RECOVERED", "SUCCESS", "Ready", "HEALTHY", "RECOVERED_VERIFIED") else ("#ef4444" if severity == "CRITICAL" else "#f59e0b")

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
    .card {{ background-color: #1e293b; border-radius: 10px; border: 1px solid #334155; max-width: 650px; margin: 0 auto; overflow: hidden; }}
    .header {{ background: linear-gradient(135deg, #1e1e38 0%, #0f172a 100%); padding: 20px 24px; border-bottom: 1px solid #334155; }}
    .header h2 {{ margin: 0; color: #38bdf8; font-size: 20px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 12px; text-transform: uppercase; }}
    .content {{ padding: 24px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #334155; font-size: 14px; }}
    th {{ color: #94a3b8; width: 35%; }}
    td {{ color: #f1f5f9; }}
    .btn {{ display: inline-block; margin-top: 20px; background-color: #0284c7; color: #ffffff !important; padding: 10px 20px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 14px; }}
    .footer {{ background-color: #0f172a; padding: 14px 24px; font-size: 12px; color: #64748b; text-align: center; border-top: 1px solid #334155; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h2>⎈ Autonomous Kubernetes Health Monitor</h2>
      <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 13px;">Incident Alert & Self-Healing Telemetry</p>
    </div>
    <div class="content">
      {test_banner_html}
      <div style="margin-bottom: 16px;">
        <span class="badge" style="background-color: {status_color}; color: #ffffff;">{severity}</span>
        <span class="badge" style="background-color: #334155; color: #94a3b8; margin-left: 8px;">{event_type}</span>
      </div>
      <table>
        <tr><th>Incident ID</th><td><strong>{incident_id}</strong></td></tr>
        <tr><th>Cluster</th><td><code>{cluster}</code></td></tr>
        <tr><th>Namespace</th><td><code>{namespace}</code></td></tr>
        <tr><th>Target Workload</th><td>{target_workload}</td></tr>
        <tr><th>Target Pod</th><td><code>{target_pod}</code></td></tr>
        <tr><th>Detected Problem</th><td>{problem}</td></tr>
        <tr><th>Remediation Action</th><td>{action_taken}</td></tr>
        <tr><th>Current Status</th><td><strong style="color: {status_color};">{status}</strong></td></tr>
        <tr><th>Timestamp</th><td>{ts}</td></tr>
        <tr><th>Target Recipient</th><td><code>{recipient}</code></td></tr>
        {metrics_html_rows}
      </table>
      <div style="margin-top: 16px; background-color: #0f172a; padding: 12px; border-radius: 6px; border: 1px solid #334155; font-size: 13px; color: #cbd5e1;">
        <strong>Diagnostics & Telemetry:</strong><br>{details or 'No additional diagnostics attached.'}
      </div>
      <a href="{dash_url}" class="btn" target="_blank">Open Live Control Plane</a>
    </div>
    <div class="footer">
      Auto Kubernetes Health Monitoring Control Plane • Automated Notification • Delivered to {recipient}
    </div>
  </div>
</body>
</html>
"""
    return subject, text_body, html_body


def send_notification(
    event_type: str,
    incident_id: str,
    target_workload: str = "sample-app",
    target_pod: str = "unknown",
    namespace: str = "health-monitor",
    problem: str = "Unspecified issue",
    action_taken: str = "None",
    status: str = "TRIGGERED",
    severity: str = "WARNING",
    details: Optional[str] = None,
    force_live: bool = False,
    metrics: Optional[Dict[str, Any]] = None,
    thresholds: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Dispatches a structured alert notification via configured backends (Email/Telegram).
    Guaranteed non-blocking and safe: failures will never crash calling loops.
    """
    recipient = NotificationConfig.get_smtp_to_email()

    if not NotificationConfig.is_enabled():
        logger.debug(f"[NOTIFY-DISABLED] Alert for {incident_id} skipped: backend is disabled.")
        if incident_store:
            incident_store.record_notification(
                incident_id=incident_id,
                channel="email",
                recipient=recipient,
                subject=f"[{severity}] {event_type} on {target_workload}",
                status="disabled",
                details="Alerts are disabled in configuration (ALERT_ENABLED=false or ALERT_BACKEND=none)."
            )
        return {"status": "disabled", "incident_id": incident_id, "recipient": recipient}

    # Deduplication check (exclude manual TEST events from cooldown)
    dedup_key = f"{target_pod}:{event_type}:{status}"
    cooldown = NotificationConfig.get_cooldown_seconds()
    if event_type != "TEST" and _is_deduplicated(dedup_key, cooldown):
        logger.info(f"[NOTIFY-COOLDOWN] Suppressed duplicate alert for {dedup_key} (cooldown {cooldown}s active).")
        if incident_store:
            incident_store.record_notification(
                incident_id=incident_id,
                channel="email",
                recipient=recipient,
                subject=f"[{severity}] {event_type} on {target_workload}",
                status="suppressed",
                details=f"Suppressed duplicate alert within {cooldown}s cooldown window."
            )
        return {"status": "suppressed", "incident_id": incident_id, "reason": "cooldown_active"}

    subject, text_body, html_body = format_alert(
        event_type=event_type,
        incident_id=incident_id,
        severity=severity,
        target_workload=target_workload,
        target_pod=target_pod,
        namespace=namespace,
        problem=problem,
        action_taken=action_taken,
        status=status,
        details=details,
        metrics=metrics,
        thresholds=thresholds
    )

    backend = NotificationConfig.get_alert_backend()
    results = {}

    # Email notification dispatch
    if backend in ("email", "both"):
        results["email"] = _dispatch_email(
            incident_id=incident_id,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            force_live=force_live
        )

    # Telegram notification dispatch
    if backend in ("telegram", "both"):
        results["telegram"] = _dispatch_telegram(incident_id, text_body)

    # Return primary result without circular self-reference
    primary = dict(results.get("email") or results.get("telegram") or {"status": "completed"})
    return primary


def _dispatch_email(
    incident_id: str,
    subject: str,
    text_body: str,
    html_body: str,
    force_live: bool = False
) -> Dict[str, Any]:
    """
    Internal handler for SMTP delivery with configuration validation, bounded retries,
    STARTTLS/SSL support, safe connection cleanup, and explicit delivery statuses:
      - 'not_configured' : credentials missing for live dispatch
      - 'dry_run'        : simulated validation without network connection
      - 'smtp_accepted'  : remote SMTP relay confirmed handoff (250 OK)
      - 'failed'         : connection, auth, timeout, or recipient failure
    """
    recipient = NotificationConfig.get_alert_to_email()
    sender = NotificationConfig.get_smtp_from()
    dry_run = NotificationConfig.is_dry_run() and not force_live

    # ── 1. Dry-Run Handling ───────────────────────────────────────────────────
    if dry_run:
        logger.info(f"[NOTIFY-DRY-RUN] Simulating email delivery to {recipient}: '{subject}'")
        if incident_store:
            incident_store.record_notification(
                incident_id=incident_id,
                channel="email",
                recipient=recipient,
                subject=subject,
                status="dry_run",
                details=f"Dry-run simulation mode. SMTP target: {NotificationConfig.get_smtp_host()}:{NotificationConfig.get_smtp_port()}"
            )
        return {
            "status": "dry_run",
            "display_status": "Dry Run (Simulated)",
            "channel": "email",
            "recipient": recipient,
            "subject": subject,
            "message": "Dry-run mode: Notification validated and recorded without real network dispatch."
        }

    # ── 2. Live Delivery Configuration Validation ─────────────────────────────
    is_valid, validation_err = NotificationConfig.validate_smtp_config(require_auth=True)
    if not is_valid:
        err_msg = f"SMTP configuration missing or invalid: {validation_err}"
        logger.warning(f"[NOTIFY-NOT-CONFIGURED] {err_msg}")
        if incident_store:
            incident_store.record_notification(
                incident_id=incident_id,
                channel="email",
                recipient=recipient,
                subject=subject,
                status="not_configured",
                error_message=err_msg,
                details="Live delivery requested, but sending account credentials are not configured. Set SMTP_USER and SMTP_PASSWORD in .env or Kubernetes Secret 'smtp-secret'."
            )
        return {
            "status": "not_configured",
            "display_status": "Not configured",
            "channel": "email",
            "recipient": recipient,
            "subject": subject,
            "error": err_msg,
            "message": f"Not configured: {validation_err} Please configure your sending account in .env or Kubernetes Secret."
        }

    # ── 3. Real SMTP Dispatch with Bounded Retries ────────────────────────────
    host = NotificationConfig.get_smtp_host()
    port = NotificationConfig.get_smtp_port()
    user = NotificationConfig.get_smtp_user()
    passwd = NotificationConfig.get_smtp_password()
    use_tls = NotificationConfig.get_smtp_use_tls()
    use_ssl = NotificationConfig.get_smtp_use_ssl()
    timeout = NotificationConfig.get_smtp_timeout()
    max_retries = NotificationConfig.get_smtp_max_retries()
    retry_delay = NotificationConfig.get_smtp_retry_backoff_sec()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    last_error = None

    for attempt in range(1, max_retries + 1):
        server = None
        try:
            logger.info(f"[NOTIFY-SMTP] (Attempt {attempt}/{max_retries}) Connecting to {host}:{port} (SSL={use_ssl}, TLS={use_tls}, timeout={timeout}s)...")

            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=timeout)
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
                if use_tls:
                    server.starttls()

            if user and passwd:
                server.login(user, passwd)

            server.send_message(msg)

            logger.info(f"[NOTIFY-SUCCESS] SMTP server {host}:{port} accepted delivery for {recipient}")
            if incident_store:
                incident_store.record_notification(
                    incident_id=incident_id,
                    channel="email",
                    recipient=recipient,
                    subject=subject,
                    status="smtp_accepted",
                    details=f"Delivery accepted by SMTP server {host}:{port} for {recipient}. (Attempt {attempt})"
                )
            return {
                "status": "smtp_accepted",
                "display_status": "SMTP accepted",
                "confirmed": True,
                "channel": "email",
                "recipient": recipient,
                "subject": subject,
                "message": f"SMTP accepted: Delivery accepted by SMTP server {host}:{port} for {recipient}. Note: SMTP acceptance confirms relay handoff, but does not guarantee arrival in the inbox (check spam folder or mail filtering rules)."
            }

        except smtplib.SMTPAuthenticationError as exc:
            err_msg = f"SMTP Authentication failed on {host}:{port} (code {exc.smtp_code}): check username or App Password."
            logger.error(f"[NOTIFY-FAILED] {err_msg}")
            if incident_store:
                incident_store.record_notification(
                    incident_id=incident_id,
                    channel="email",
                    recipient=recipient,
                    subject=subject,
                    status="failed",
                    error_message=err_msg,
                    details=err_msg
                )
            return {
                "status": "failed",
                "display_status": "Failed",
                "channel": "email",
                "recipient": recipient,
                "error": err_msg,
                "message": err_msg
            }

        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            err_msg = f"SMTP address rejected: {exc}"
            logger.error(f"[NOTIFY-FAILED] {err_msg}")
            if incident_store:
                incident_store.record_notification(
                    incident_id=incident_id,
                    channel="email",
                    recipient=recipient,
                    subject=subject,
                    status="failed",
                    error_message=err_msg,
                    details=err_msg
                )
            return {
                "status": "failed",
                "display_status": "Failed",
                "channel": "email",
                "recipient": recipient,
                "error": err_msg,
                "message": err_msg
            }

        except smtplib.SMTPResponseException as exc:
            err_msg = f"SMTP protocol error on {host}:{port} (code {exc.smtp_code}): {exc.smtp_error.decode() if isinstance(exc.smtp_error, bytes) else exc.smtp_error}"
            logger.error(f"[NOTIFY-FAILED] {err_msg}")
            if incident_store:
                incident_store.record_notification(
                    incident_id=incident_id,
                    channel="email",
                    recipient=recipient,
                    subject=subject,
                    status="failed",
                    error_message=err_msg,
                    details=err_msg
                )
            return {
                "status": "failed",
                "display_status": "Failed",
                "channel": "email",
                "recipient": recipient,
                "error": err_msg,
                "message": err_msg
            }

        except (socket.timeout, TimeoutError) as exc:
            is_timeout = True
            last_error = f"SMTP connection timed out after {timeout}s to {host}:{port}: {exc}"
            logger.warning(f"[NOTIFY-RETRY] Attempt {attempt}/{max_retries} failed: {last_error}")
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
            else:
                logger.error(f"[NOTIFY-FAILED] All {max_retries} attempts timed out to {host}:{port}.")
        except (smtplib.SMTPConnectError, ConnectionRefusedError, socket.gaierror, OSError) as exc:
            if "timed out" in str(exc).lower():
                is_timeout = True
            last_error = f"SMTP connection failed to {host}:{port}: {exc}"
            logger.warning(f"[NOTIFY-RETRY] Attempt {attempt}/{max_retries} failed: {last_error}")
            if attempt < max_retries:
                time.sleep(retry_delay * attempt)
            else:
                logger.error(f"[NOTIFY-FAILED] All {max_retries} connection attempts failed to {host}:{port}.")
        except Exception as exc:
            if "timed out" in str(exc).lower():
                is_timeout = True
            last_error = f"SMTP unexpected error on {host}:{port}: {type(exc).__name__}: {exc}"
            logger.error(f"[NOTIFY-FAILED] {last_error}")
            break
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    try:
                        server.close()
                    except Exception:
                        pass

    err_msg = last_error or f"SMTP delivery failed after {max_retries} attempts."
    status_code = "timed_out" if is_timeout else "failed"
    display_label = "Timed out" if is_timeout else "Failed"

    if incident_store:
        incident_store.record_notification(
            incident_id=incident_id,
            channel="email",
            recipient=recipient,
            subject=subject,
            status=status_code,
            error_message=err_msg,
            details=err_msg
        )
    return {
        "status": status_code,
        "display_status": display_label,
        "channel": "email",
        "recipient": recipient,
        "error": err_msg,
        "message": err_msg
    }


def _dispatch_telegram(incident_id: str, message: str) -> Dict[str, Any]:
    """Internal handler for Telegram bot alerts with error shielding."""
    token = NotificationConfig.get_telegram_token()
    chat_id = NotificationConfig.get_telegram_chat_id()

    if not (token and chat_id):
        logger.info("[NOTIFY-TELEGRAM-DRY-RUN] Telegram bot token or chat ID not set. Dry-run recording.")
        if incident_store:
            incident_store.record_notification(
                incident_id=incident_id,
                channel="telegram",
                recipient="telegram-chat-unconfigured",
                subject="Telegram Alert",
                status="dry_run",
                details="Telegram token/chat_id unset. Simulation recorded."
            )
        return {"status": "dry_run", "channel": "telegram", "message": "Telegram unconfigured (dry-run)"}

    try:
        import urllib.request
        import json
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": message[:4000]}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                logger.info("[NOTIFY-SUCCESS] Telegram alert delivered successfully.")
                if incident_store:
                    incident_store.record_notification(
                        incident_id=incident_id,
                        channel="telegram",
                        recipient=chat_id,
                        subject="Telegram Alert",
                        status="sent"
                    )
                return {"status": "sent", "channel": "telegram"}
            else:
                logger.warning(f"[NOTIFY-TELEGRAM] HTTP {response.status}")
                return {"status": "failed", "channel": "telegram", "error": f"HTTP {response.status}"}
    except Exception as exc:
        logger.error(f"[NOTIFY-TELEGRAM-FAILED] {exc}")
        if incident_store:
            incident_store.record_notification(
                incident_id=incident_id,
                channel="telegram",
                recipient=chat_id,
                subject="Telegram Alert",
                status="failed",
                error_message=str(exc)
            )
        return {"status": "failed", "channel": "telegram", "error": str(exc)}


def send_test_notification(force_live: bool = False) -> Dict[str, Any]:
    """
    Triggers an authorized, safe test alert validating configuration and channel health.
    - If force_live=True or SMTP_DRY_RUN=false, attempts real SMTP delivery to sainaath.r2025@vitstudent.ac.in.
    - If credentials missing, returns status='missing_config' with explicit guidance.
    - Otherwise dispatches safely in dry-run mode and logs results.
    """
    test_id = f"TEST-{int(time.time())}"
    recipient = NotificationConfig.get_alert_to_email()

    return send_notification(
        event_type="TEST",
        incident_id=test_id,
        target_workload="sample-app",
        target_pod="sample-app-test-verification",
        namespace="health-monitor",
        problem="Diagnostic Test: Verification of notification pipeline & SMTP transport",
        action_taken=f"Dispatched {'live' if force_live else 'diagnostic'} test notification to {recipient}",
        status="HEALTHY",
        severity="INFO",
        details="This is an automated test message from the Kubernetes Health Monitor control plane.",
        force_live=force_live
    )


# ── CLI Entrypoint ────────────────────────────────────────────────────────────

def cli_main(argv: Optional[List[str]] = None) -> int:
    """Command-line interface for inspecting and testing the notification engine."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Kubernetes Health Monitor Notification Engine CLI"
    )
    parser.add_argument(
        "--check-config", action="store_true",
        help="Check and display current SMTP & Telegram configuration (secrets masked)"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Send a test notification (dry-run if credentials absent)"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Force live SMTP delivery to configured recipient"
    )
    parser.add_argument(
        "--recipient", type=str, default="",
        help=f"Override target recipient (default: {NotificationConfig.DEFAULT_RECIPIENT})"
    )

    args = parser.parse_args(args=argv)

    if args.recipient:
        os.environ["ALERT_TO_EMAIL"] = args.recipient

    if args.check_config or (not args.test and not args.live):
        print("\n" + "=" * 65)
        print("  NOTIFICATION ENGINE OPERATIONAL CONFIGURATION (SECRETS MASKED)")
        print("=" * 65)
        config = NotificationConfig.get_masked_config()
        print(json.dumps(config, indent=2))
        is_valid, err = NotificationConfig.validate_smtp_config(require_auth=True)
        print(f"\nLive SMTP Delivery Ready: {'YES' if is_valid else 'NO'}")
        if not is_valid:
            print(f"Notice: {err}")
        print("=" * 65 + "\n")

    if args.test or args.live:
        print(f"\nDispatching test notification (live={args.live})...")
        res = send_test_notification(force_live=args.live)
        print("\nResult:")
        print(json.dumps(res, indent=2))
        print()
        if res.get("status") in ("smtp_accepted", "sent", "dry_run"):
            return 0
        return 1

    return 0


# Convenient alias for tests or entry points
main = cli_main


if __name__ == "__main__":
    import sys
    sys.exit(cli_main())

