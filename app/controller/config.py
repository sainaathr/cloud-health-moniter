"""
config.py — Central configuration for the Health Monitor Controller.
All values can be overridden via environment variables so that no
secrets are ever hard-coded.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Cluster settings ──────────────────────────────────────────────────────────
# Namespace that the controller watches. Default: "health-monitor"
WATCH_NAMESPACE = os.getenv("WATCH_NAMESPACE", "health-monitor")

# How often (seconds) the controller polls the cluster
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

# ── Resource thresholds (%) ───────────────────────────────────────────────────
CPU_WARNING_THRESHOLD  = float(os.getenv("CPU_WARNING_THRESHOLD",  "70"))
CPU_CRITICAL_THRESHOLD = float(os.getenv("CPU_CRITICAL_THRESHOLD", "85"))

MEM_WARNING_THRESHOLD  = float(os.getenv("MEM_WARNING_THRESHOLD",  "70"))
MEM_CRITICAL_THRESHOLD = float(os.getenv("MEM_CRITICAL_THRESHOLD", "85"))

# ── Self-healing settings ─────────────────────────────────────────────────────
# Maximum number of delete attempts per pod before giving up
MAX_RECOVERY_ATTEMPTS = int(os.getenv("MAX_RECOVERY_ATTEMPTS", "3"))

# Seconds to wait between recovery attempts (cooldown)
RECOVERY_COOLDOWN = int(os.getenv("RECOVERY_COOLDOWN", "60"))

# Seconds to wait for a replacement pod to become Ready
RECOVERY_TIMEOUT = int(os.getenv("RECOVERY_TIMEOUT", "120"))

# Restart count that triggers a CrashLoopBackOff warning
RESTART_THRESHOLD = int(os.getenv("RESTART_THRESHOLD", "5"))

# ── Alerting & Email Notifications ───────────────────────────────────────────
# Default backend: "email" (or "telegram", "both", "none")
ALERT_BACKEND = os.getenv("ALERT_BACKEND", "email")
ALERT_ENABLED = os.getenv("ALERT_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")

# Default notification recipient
DEFAULT_RECIPIENT = "sainaath.r2025@vitstudent.ac.in"
SMTP_TO_EMAIL = os.getenv("SMTP_TO_EMAIL") or os.getenv("ALERT_TO_EMAIL") or os.getenv("SMTP_TO") or DEFAULT_RECIPIENT
ALERT_TO_EMAIL = SMTP_TO_EMAIL

# SMTP settings
SMTP_HOST         = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT         = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME     = os.getenv("SMTP_USERNAME") or os.getenv("SMTP_USER", "")
SMTP_USER         = SMTP_USERNAME
SMTP_PASSWORD     = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL   = os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_FROM") or os.getenv("SMTP_SENDER") or "k8s-monitor@cloud-health.local"
SMTP_FROM         = SMTP_FROM_EMAIL
SMTP_USE_TLS      = os.getenv("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes")
SMTP_USE_SSL      = os.getenv("SMTP_USE_SSL", "false").strip().lower() in ("1", "true", "yes")
SMTP_TIMEOUT_SECONDS = int(os.getenv("SMTP_TIMEOUT_SECONDS") or os.getenv("SMTP_TIMEOUT_SEC") or "10")
SMTP_TIMEOUT_SEC  = SMTP_TIMEOUT_SECONDS
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC", "300"))

# Telegram — obtained from @BotFather and your chat/group id
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE = os.getenv("LOG_FILE", "/app/logs/health-monitor.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
