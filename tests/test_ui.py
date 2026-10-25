import unittest
import json
from unittest.mock import patch, MagicMock
from app.ui.app import app, app_state, MEMORY_ALLOCATED
import app.ui.app as app_module

class TestUI(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app_state["is_ready"] = True
        app_state["memory_leak"].clear()
        MEMORY_ALLOCATED.set(0)

    def test_index_page_renders(self):
        """Test that the index dashboard page renders with HTTP 200 and expected markup."""
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"K8s Health Monitor & Self-Healing Engine", res.data)
        self.assertIn(b"Cluster Pod Status", res.data)
        self.assertIn(b"Incident Timeline", res.data)

    def test_memory_allocation_bounded(self):
        """Test that memory allocation is bound safely at 50MB and increments properly."""
        # Initial allocation
        res = self.client.get('/api/memory-test?size_mb=10')
        self.assertEqual(res.status_code, 200)
        
        # Test cumulative limit (50MB) 
        # Adding 41MB should exceed 10+41 = 51MB > 50MB
        res2 = self.client.get('/api/memory-test?size_mb=41')
        self.assertEqual(res2.status_code, 400)
        self.assertIn(b"Limit: 50MB", res2.data)
        
    def test_memory_invalid_input(self):
        """Test negative and malformed inputs."""
        res = self.client.get('/api/memory-test?size_mb=-5')
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"negative", res.data)
        
        res2 = self.client.get('/api/memory-test?size_mb=abc')
        self.assertEqual(res2.status_code, 400)
        self.assertIn(b"invalid", res2.data)

    def test_memory_reset(self):
        """Test that size_mb=0 correctly clears all trackings."""
        self.client.get('/api/memory-test?size_mb=10')
        res = self.client.get('/api/memory-test?size_mb=0')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(app_state["memory_leak"]), 0)
        
    def test_chaos_oom_security_gate_without_exhausting_memory(self):
        """
        Test that /api/chaos/oom is disabled by default, requires server authorization,
        cannot be bypassed by query parameters, and tests safety gate without 200MB allocation.
        """
        # 1. Disabled by default
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', False):
            res = self.client.post('/api/chaos/oom')
            self.assertEqual(res.status_code, 403)
            self.assertIn(b"disabled by server policy", res.data)
            
            # Query parameter alone must NOT bypass
            res_bypass = self.client.post('/api/chaos/oom?allow_oom=true')
            self.assertEqual(res_bypass.status_code, 403)

        # 2. Enabled on server, but missing or invalid token
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', True):
            res_no_token = self.client.post('/api/chaos/oom')
            self.assertEqual(res_no_token.status_code, 401)

            res_bad_token = self.client.post('/api/chaos/oom', headers={"X-Chaos-Token": "bad-token"})
            self.assertEqual(res_bad_token.status_code, 401)

            # 3. Authorized dry-run test: tests safety gate without allocating 200MB
            res_auth = self.client.post(
                '/api/chaos/oom',
                headers={
                    "X-Chaos-Token": app_module.CHAOS_AUTH_TOKEN,
                    "X-Dry-Run": "true"
                }
            )
            self.assertEqual(res_auth.status_code, 200)
            data = json.loads(res_auth.data)
            self.assertEqual(data.get("mode"), "dry_run")
            self.assertIn("Safety gate passed without memory exhaustion", data.get("message", ""))
            # Crucial check: verify NO 200MB was allocated into memory
            self.assertEqual(len(app_state["memory_leak"]), 0)

    def test_chaos_crash_security_gate(self):
        """Test that /api/chaos/crash is disabled by default and requires server authorization."""
        # 1. Disabled by default
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', False):
            res = self.client.post('/api/chaos/crash')
            self.assertEqual(res.status_code, 403)
            # Query param confirm alone must NOT bypass
            res2 = self.client.post('/api/chaos/crash?confirm=true')
            self.assertEqual(res2.status_code, 403)

        # 2. Authorized dry-run
        with patch.object(app_module, 'ENABLE_DESTRUCTIVE_CHAOS', True):
            res_auth = self.client.post(
                '/api/chaos/crash',
                headers={
                    "X-Chaos-Token": app_module.CHAOS_AUTH_TOKEN,
                    "X-Dry-Run": "true"
                }
            )
            self.assertEqual(res_auth.status_code, 200)
            data = json.loads(res_auth.data)
            self.assertEqual(data.get("mode"), "dry_run")

    def test_metrics_collection_5xx_and_duration(self):
        """Test that the application genuinely instruments 5xx records and duration seconds."""
        # Force a 503 through readiness failure
        self.client.post('/api/chaos/readiness')
        res_503 = self.client.get('/health/ready')
        self.assertEqual(res_503.status_code, 503)

        # Restore
        self.client.post('/api/chaos/readiness')

        # Check /metrics payload
        res_metrics = self.client.get('/metrics')
        metrics_data = res_metrics.data.decode('utf-8')
        
        # Verify 503 response got counted
        self.assertIn('http_requests_total{endpoint="/health/ready",method="GET",status_code="503"} 1.0', metrics_data)
        # Verify duration stats are emitted
        self.assertIn('http_request_duration_seconds_count', metrics_data)
        self.assertIn('http_request_duration_seconds_sum', metrics_data)

    def test_incident_persistence_and_timeline(self):
        """Test that toggling readiness registers an incident in SQLite and /api/incidents returns it."""
        # Toggle readiness to False
        res = self.client.post('/api/chaos/readiness')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertFalse(data["is_ready"])
        self.assertIsNotNone(data["incident_id"])

        # Fetch incidents
        res_inc = self.client.get('/api/incidents')
        self.assertEqual(res_inc.status_code, 200)
        inc_data = json.loads(res_inc.data)
        self.assertTrue(len(inc_data["incidents"]) > 0)
        latest = inc_data["incidents"][0]
        self.assertIn("Readiness Failure", latest["incident_type"])
        self.assertEqual(latest["status"], "UNREADY")

        # Toggle readiness back to True
        res_rec = self.client.post('/api/chaos/readiness')
        self.assertEqual(res_rec.status_code, 200)

        # Check incidents updated
        res_inc2 = self.client.get('/api/incidents')
        inc_data2 = json.loads(res_inc2.data)
        recovered = [i for i in inc_data2["incidents"] if i["incident_id"] == data["incident_id"]]
        self.assertTrue(len(recovered) > 0)
        self.assertEqual(recovered[0]["status"], "RECOVERED")

    def test_chaos_reset_endpoint(self):
        """Test that /api/chaos/reset restores readiness and clears memory."""
        # Break readiness and allocate memory
        self.client.post('/api/chaos/readiness')
        self.client.get('/api/memory-test?size_mb=10')
        self.assertFalse(app_state["is_ready"])
        self.assertTrue(len(app_state["memory_leak"]) > 0)

        # Call reset
        res = self.client.post('/api/chaos/reset')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(data["is_ready"])
        self.assertEqual(data["memory_allocated_bytes"], 0)
        self.assertTrue(app_state["is_ready"])
        self.assertEqual(len(app_state["memory_leak"]), 0)

    def test_cluster_endpoints_explicit_unavailable_state(self):
        """
        Verify that Kubernetes connection failures return an explicit unavailable state,
        and NEVER report a fabricated Running pod or healthy cluster.
        """
        res_overview = self.client.get('/api/cluster/overview')
        self.assertEqual(res_overview.status_code, 200)
        overview = json.loads(res_overview.data)
        
        # When Kubernetes is disconnected:
        if not overview.get("k8s_connected"):
            self.assertEqual(overview["cluster_status"], "UNAVAILABLE")
            self.assertEqual(overview["pods_healthy"], 0)
            self.assertEqual(overview["pods_total"], 0)
            self.assertEqual(overview["replicas_desired"], 0)
            self.assertEqual(overview["replicas_available"], 0)
            self.assertEqual(overview["controller_status"], "UNAVAILABLE")

            # Verify /api/cluster/pods returns empty list without fabricated Running pod
            res_pods = self.client.get('/api/cluster/pods')
            self.assertEqual(res_pods.status_code, 200)
            pods_data = json.loads(res_pods.data)
            self.assertFalse(pods_data.get("k8s_connected"))
            self.assertEqual(pods_data.get("count"), 0)
            self.assertEqual(pods_data.get("pods"), [])
            self.assertIn("error", pods_data)

    def test_cluster_endpoints_mocked_live_state(self):
        """Test cluster endpoints correctly parse live Kubernetes data when connected."""
        mock_pod = MagicMock()
        mock_pod.metadata.name = "sample-app-prod-123"
        mock_pod.metadata.labels = {"app": "sample-app"}
        mock_pod.status.phase = "Running"
        cs = MagicMock()
        cs.ready = True
        cs.restart_count = 0
        cs.last_state.terminated = None
        mock_pod.status.container_statuses = [cs]
        mock_pod.spec.node_name = "minikube"
        mock_pod.metadata.creation_timestamp = None

        with patch('kubernetes.config.load_incluster_config'), \
             patch('kubernetes.client.CoreV1Api') as mock_core:
            mock_core.return_value.list_namespaced_pod.return_value.items = [mock_pod]
            
            res_pods = self.client.get('/api/cluster/pods')
            self.assertEqual(res_pods.status_code, 200)
            pods_data = json.loads(res_pods.data)
            self.assertTrue(pods_data["k8s_connected"])
            self.assertEqual(pods_data["count"], 1)
            self.assertEqual(pods_data["pods"][0]["name"], "sample-app-prod-123")
            self.assertTrue(pods_data["pods"][0]["ready"])

    def test_action_logs_endpoint(self):
        """Test /api/logs returns chronological action logs."""
        res = self.client.get('/api/logs')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("logs", data)

if __name__ == '__main__':
    unittest.main()
