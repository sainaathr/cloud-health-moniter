"""
incident_store.py — Lightweight SQLite-backed Persistent Incident & Action Log Store
===================================================================================
Persists health incidents, failure events, and recovery actions across page reloads
and container restarts.
Accurately categorizes and distinguishes:
  - SIMULATION: Simulated incidents and local recovery
  - K8S_READINESS: Real Kubernetes readiness probe failures & traffic isolation
  - NATIVE_K8S: Native Kubernetes container restarts (OOMKilled / Kubelet)
  - CONTROLLER_ACTION: Recovery actions taken by custom controller (pod deletion)
  - VERIFIED_REPLACEMENT: Verified replacement pod becoming Running & Ready
"""

import sqlite3
import os
import time
import datetime
import contextlib
from typing import Optional, List, Dict, Any

def get_db_path() -> str:
    return os.getenv("INCIDENT_DB_PATH", os.path.join(os.path.dirname(__file__), "incidents.db"))

@contextlib.contextmanager
def get_db():
    """Context manager for SQLite connections that guarantees clean closure and schema readiness."""
    path = get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # Guarantee core tables exist even on dynamically created test DBs
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT UNIQUE NOT NULL,
                incident_type TEXT NOT NULL,
                target_workload TEXT NOT NULL,
                target_pod TEXT NOT NULL,
                detection_source TEXT NOT NULL,
                detection_time TEXT NOT NULL,
                status TEXT NOT NULL,
                recovery_action TEXT,
                recovery_result TEXT,
                recovery_duration_sec REAL DEFAULT 0.0,
                evidence TEXT,
                category TEXT DEFAULT 'GENERAL',
                is_simulated INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT,
                channel TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                details TEXT,
                sent_at TEXT NOT NULL
            )
        """)
        conn.commit()
        yield conn
    finally:
        conn.close()

def get_connection() -> sqlite3.Connection:
    """Legacy helper for callers expecting an open connection."""
    path = get_db_path()
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

DB_PATH = get_db_path()

def init_db():
    """Initializes tables and applies backward-compatible column upgrades."""
    path = get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT UNIQUE NOT NULL,
                incident_type TEXT NOT NULL,
                target_workload TEXT NOT NULL,
                target_pod TEXT NOT NULL,
                detection_source TEXT NOT NULL,
                detection_time TEXT NOT NULL,
                status TEXT NOT NULL,
                recovery_action TEXT,
                recovery_result TEXT,
                recovery_duration_sec REAL DEFAULT 0.0,
                evidence TEXT,
                category TEXT DEFAULT 'GENERAL',
                is_simulated INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT,
                channel TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                details TEXT,
                sent_at TEXT NOT NULL
            )
        """)
        conn.commit()

        # Migrate existing tables if category / is_simulated columns missing
        cursor = conn.execute("PRAGMA table_info(incidents)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "category" not in columns:
            conn.execute("ALTER TABLE incidents ADD COLUMN category TEXT DEFAULT 'GENERAL'")
            conn.commit()
        if "is_simulated" not in columns:
            conn.execute("ALTER TABLE incidents ADD COLUMN is_simulated INTEGER DEFAULT 0")
            conn.commit()

# Initialize upon module load
init_db()

def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def record_incident(
    incident_type: str,
    target_pod: str,
    detection_source: str,
    target_workload: str = "sample-app",
    status: str = "TRIGGERED",
    recovery_action: str = "Pending",
    recovery_result: str = "IN_PROGRESS",
    evidence: str = "",
    category: str = "GENERAL",
    is_simulated: bool = False,
    incident_id: Optional[str] = None
) -> Dict[str, Any]:
    """Create a new persistent incident record."""
    if not incident_id:
        ts_num = int(time.time() * 1000) % 1000000
        incident_id = f"INC-{datetime.datetime.now().strftime('%Y%m%d')}-{ts_num:06d}"

    now = _now_iso()
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO incidents (
                incident_id, incident_type, target_workload, target_pod,
                detection_source, detection_time, status, recovery_action,
                recovery_result, recovery_duration_sec, evidence,
                category, is_simulated, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            incident_id, incident_type, target_workload, target_pod,
            detection_source, now, status, recovery_action,
            recovery_result, 0.0, evidence,
            category, 1 if is_simulated else 0, now, now
        ))
        conn.commit()

    log_action("WARN", detection_source, f"Incident {incident_id} [{category}] recorded: {incident_type} on {target_pod}")
    return get_incident(incident_id)

def update_incident(
    incident_id: str,
    status: Optional[str] = None,
    recovery_action: Optional[str] = None,
    recovery_result: Optional[str] = None,
    recovery_duration_sec: Optional[float] = None,
    evidence: Optional[str] = None,
    category: Optional[str] = None,
    is_simulated: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
    """Update an existing incident with recovery or status transition."""
    existing = get_incident(incident_id)
    if not existing:
        return None

    now = _now_iso()
    new_status = status if status is not None else existing["status"]
    new_action = recovery_action if recovery_action is not None else existing["recovery_action"]
    new_result = recovery_result if recovery_result is not None else existing["recovery_result"]
    new_dur = recovery_duration_sec if recovery_duration_sec is not None else existing["recovery_duration_sec"]
    new_ev = evidence if evidence is not None else existing["evidence"]
    new_cat = category if category is not None else existing.get("category", "GENERAL")
    new_sim = (1 if is_simulated else 0) if is_simulated is not None else existing.get("is_simulated", 0)

    with get_db() as conn:
        conn.execute("""
            UPDATE incidents SET
                status = ?,
                recovery_action = ?,
                recovery_result = ?,
                recovery_duration_sec = ?,
                evidence = ?,
                category = ?,
                is_simulated = ?,
                updated_at = ?
            WHERE incident_id = ?
        """, (new_status, new_action, new_result, new_dur, new_ev, new_cat, new_sim, now, incident_id))
        conn.commit()

    log_action("INFO", "Self-Healing", f"Incident {incident_id} updated: status={new_status}, result={new_result}")
    return get_incident(incident_id)

def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_incidents(limit: int = 50, category: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_db() as conn:
        if category:
            cursor = conn.execute("SELECT * FROM incidents WHERE category = ? ORDER BY id DESC LIMIT ?", (category, limit))
        else:
            cursor = conn.execute("SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

def log_action(level: str, source: str, message: str, details: str = "") -> Dict[str, Any]:
    now = _now_iso()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO action_logs (timestamp, level, source, message, details)
            VALUES (?, ?, ?, ?, ?)
        """, (now, level, source, message, details))
        conn.commit()
    return {"timestamp": now, "level": level, "source": source, "message": message, "details": details}

def get_action_logs(limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM action_logs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

def record_notification(
    incident_id: Optional[str],
    channel: str,
    recipient: str,
    subject: str,
    status: str,
    error_message: Optional[str] = None,
    details: Optional[str] = None
) -> Dict[str, Any]:
    """Persist notification delivery record associated with an incident."""
    now = _now_iso()
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO notifications (
                incident_id, channel, recipient, subject, status, error_message, details, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            incident_id or "", channel, recipient, subject,
            status, error_message or "", details or "", now
        ))
        conn.commit()
        notif_id = cursor.lastrowid
    log_action(
        "INFO" if status in ("smtp_accepted", "sent", "dry_run") else "WARN",
        "Notification",
        f"Notification [{channel}] {status} -> {recipient}: {subject}"
    )
    return {
        "id": notif_id,
        "incident_id": incident_id,
        "channel": channel,
        "recipient": recipient,
        "subject": subject,
        "status": status,
        "error_message": error_message,
        "details": details,
        "sent_at": now
    }

def get_notifications(limit: int = 50, incident_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve persisted notification delivery logs."""
    with get_db() as conn:
        if incident_id:
            cursor = conn.execute(
                "SELECT * FROM notifications WHERE incident_id = ? ORDER BY id DESC LIMIT ?",
                (incident_id, limit)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT ?",
                (limit,)
            )
        return [dict(r) for r in cursor.fetchall()]

def get_notification_summary() -> Dict[str, Any]:
    """Retrieve summary of last test attempt, last success, and last error."""
    with get_db() as conn:
        last_test = conn.execute(
            "SELECT * FROM notifications WHERE incident_id LIKE 'TEST%' OR subject LIKE '%Test%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_success = conn.execute(
            "SELECT * FROM notifications WHERE status IN ('smtp_accepted', 'sent') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_error = conn.execute(
            "SELECT * FROM notifications WHERE status IN ('failed', 'timed_out') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "last_test": dict(last_test) if last_test else None,
            "last_success": dict(last_success) if last_success else None,
            "last_error": dict(last_error) if last_error else None
        }
