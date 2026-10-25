import unittest
import os
import tempfile
import importlib

class TestIncidentStore(unittest.TestCase):
    def setUp(self):
        # Create a temporary database for test isolation
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        os.environ["INCIDENT_DB_PATH"] = self.temp_db.name

        import app.ui.incident_store as incident_store
        self.store = importlib.reload(incident_store)
        self.store.init_db()

    def tearDown(self):
        try:
            if os.path.exists(self.temp_db.name):
                os.remove(self.temp_db.name)
        except Exception:
            pass

    def test_record_and_get_incident(self):
        inc = self.store.record_incident(
            incident_type="CrashLoopBackOff",
            target_pod="sample-app-998877",
            detection_source="K8s Controller",
            status="TRIGGERED",
            evidence="Restart count 5 exceeds threshold",
            category="CONTROLLER_ACTION",
            is_simulated=False
        )
        self.assertIsNotNone(inc)
        self.assertTrue(inc["incident_id"].startswith("INC-"))
        self.assertEqual(inc["incident_type"], "CrashLoopBackOff")
        self.assertEqual(inc["target_pod"], "sample-app-998877")
        self.assertEqual(inc["status"], "TRIGGERED")
        self.assertEqual(inc["category"], "CONTROLLER_ACTION")
        self.assertEqual(inc["is_simulated"], 0)

        fetched = self.store.get_incident(inc["incident_id"])
        self.assertEqual(fetched["incident_id"], inc["incident_id"])
        self.assertEqual(fetched["status"], "TRIGGERED")
        self.assertEqual(fetched["category"], "CONTROLLER_ACTION")

    def test_update_incident(self):
        inc = self.store.record_incident(
            incident_type="Readiness Failure",
            target_pod="sample-app-1234",
            detection_source="Readiness Probe",
            status="UNREADY",
            category="K8S_READINESS"
        )
        updated = self.store.update_incident(
            inc["incident_id"],
            status="RECOVERED",
            recovery_action="Traffic restored",
            recovery_result="SUCCESS",
            recovery_duration_sec=4.5,
            evidence="Health check returned 200 OK",
            category="K8S_READINESS"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "RECOVERED")
        self.assertEqual(updated["recovery_action"], "Traffic restored")
        self.assertEqual(updated["recovery_result"], "SUCCESS")
        self.assertEqual(updated["recovery_duration_sec"], 4.5)
        self.assertEqual(updated["category"], "K8S_READINESS")

    def test_get_incidents_ordering_and_filtering(self):
        self.store.record_incident("Inc1", "pod1", "test", category="SIMULATION", is_simulated=True, incident_id="INC-001")
        self.store.record_incident("Inc2", "pod2", "test", category="K8S_READINESS", is_simulated=False, incident_id="INC-002")
        self.store.record_incident("Inc3", "pod3", "test", category="SIMULATION", is_simulated=True, incident_id="INC-003")

        incidents = self.store.get_incidents(limit=2)
        self.assertEqual(len(incidents), 2)
        # Most recent first
        self.assertEqual(incidents[0]["incident_id"], "INC-003")
        self.assertEqual(incidents[1]["incident_id"], "INC-002")

        # Category filtering
        sim_incidents = self.store.get_incidents(category="SIMULATION")
        self.assertEqual(len(sim_incidents), 2)
        self.assertTrue(all(i["category"] == "SIMULATION" for i in sim_incidents))

    def test_action_logs(self):
        self.store.log_action("INFO", "Controller", "Started pod reconciliation", "Pod 1")
        self.store.log_action("ERROR", "Kubelet", "Readiness probe failed", "503 Service Unavailable")

        logs = self.store.get_action_logs(limit=10)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["level"], "ERROR")
        self.assertEqual(logs[1]["level"], "INFO")

if __name__ == '__main__':
    unittest.main()
