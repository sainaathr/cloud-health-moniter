import unittest
# Ensure the root dir is in the python path
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app', 'controller')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from unittest.mock import MagicMock, patch

from app.controller.health_monitor import evaluate_pod, config, _recovery_tracker, attempt_recovery

class MockContainerStateWaiting:
    def __init__(self, reason):
        self.reason = reason

class MockContainerStateTerminated:
    def __init__(self, reason):
        self.reason = reason

class MockContainerStateRunning:
    pass

class MockContainerStateContext:
    def __init__(self, waiting=None, terminated=None, running=None):
        self.waiting = waiting
        self.terminated = terminated
        self.running = running

class MockContainerStatus:
    def __init__(self, restart_count, state=None, last_state=None):
        self.restart_count = restart_count
        self.state = state
        self.last_state = last_state

class MockPodStatus:
    def __init__(self, phase, container_statuses=None):
        self.phase = phase
        self.container_statuses = container_statuses

class MockPod:
    def __init__(self, name, phase, container_statuses=None):
        self.metadata = MagicMock()
        self.metadata.name = name
        self.metadata.namespace = "health-monitor"
        self.metadata.labels = {"app": "sample-app"}
        self.metadata.owner_references = []
        self.status = MockPodStatus(phase, container_statuses)

class TestController(unittest.TestCase):
    def setUp(self):
        _recovery_tracker.clear()
    
    def test_running_healthy_pod(self):
        pod = MockPod("healthy-pod", "Running", [])
        result = evaluate_pod(pod)
        self.assertTrue(result["healthy"])
        
    def test_crashloopbackoff_pod(self):
        cs = MockContainerStatus(5, state=MockContainerStateContext(waiting=MockContainerStateWaiting("CrashLoopBackOff")))
        pod = MockPod("crash-pod", "Running", [cs])
        result = evaluate_pod(pod)
        self.assertFalse(result["healthy"])
        self.assertEqual(result["reason"], "CrashLoopBackOff")

    def test_oomkilled_handled_by_k8s(self):
        # A pod that was OOMKilled but is currently Running successfully.
        # This pod should remain healthy and report k8s_recovered_oom=True
        cs = MockContainerStatus(
            restart_count=1,
            state=MockContainerStateContext(running=MockContainerStateRunning()),
            last_state=MockContainerStateContext(terminated=MockContainerStateTerminated("OOMKilled"))
        )
        pod = MockPod("oom-pod", "Running", [cs])
        
        # Override RESTART_THRESHOLD just in case
        config.RESTART_THRESHOLD = 5
        
        result = evaluate_pod(pod)
        self.assertTrue(result["k8s_recovered_oom"])
        self.assertTrue(result["healthy"])
        self.assertEqual(result["reason"], "Running")

    def test_oomkilled_crashing_pod(self):
        # A pod that was OOMKilled but is currently still in CrashLoopBackOff.
        cs = MockContainerStatus(
            restart_count=10,
            state=MockContainerStateContext(waiting=MockContainerStateWaiting("CrashLoopBackOff")),
            last_state=MockContainerStateContext(terminated=MockContainerStateTerminated("OOMKilled"))
        )
        pod = MockPod("crashing-oom-pod", "Running", [cs])
        
        result = evaluate_pod(pod)
        self.assertFalse(result["k8s_recovered_oom"]) # Not recovered natively since it's waiting!
        self.assertFalse(result["healthy"])
        self.assertEqual(result["reason"], "OOMKilled")

    def test_controller_self_protection(self):
        # Controller pod itself must be detected and protected
        pod = MockPod("health-monitor-controller-7f4b89-abc", "Running", [])
        pod.metadata.labels = {"app": "health-monitor"}
        result = evaluate_pod(pod)
        self.assertTrue(result["is_controller"])
        self.assertTrue(result["healthy"])

    def test_k8s_native_restart_running_ready(self):
        # Pod restarted natively by K8s and is currently Running + Ready
        cs = MockContainerStatus(
            restart_count=2,
            state=MockContainerStateContext(running=MockContainerStateRunning()),
        )
        cs.ready = True
        pod = MockPod("sample-app-native-rec", "Running", [cs])
        result = evaluate_pod(pod)
        self.assertTrue(result["k8s_recovered_restart"])
        self.assertTrue(result["healthy"])

    def test_wait_for_replacement_filters_wrong_deployment(self):
        from app.controller.health_monitor import _wait_for_replacement
        
        # Candidate pod belongs to 'other-service'
        candidate = MockPod("other-service-pod-xyz", "Running", [])
        cs = MockContainerStatus(0, state=MockContainerStateContext(running=MockContainerStateRunning()))
        cs.ready = True
        candidate.status.container_statuses = [cs]
        candidate.metadata.labels = {"app": "other-service"}
        candidate.metadata.deletion_timestamp = None
        
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value.items = [candidate]
        
        # Configure timeout to be short so test doesn't wait
        original_timeout = config.RECOVERY_TIMEOUT
        config.RECOVERY_TIMEOUT = 1
        try:
            # We are waiting for 'sample-app' deployment
            found = _wait_for_replacement("sample-app", "health-monitor", mock_v1, apps_v1=None, deleted_pod_name="old-pod")
            self.assertFalse(found, "Should NOT declare success for a pod from unrelated deployment")
        finally:
            config.RECOVERY_TIMEOUT = original_timeout

    def test_wait_for_replacement_accepts_matching_deployment(self):
        from app.controller.health_monitor import _wait_for_replacement
        
        # Candidate pod belongs to 'sample-app'
        candidate = MockPod("sample-app-new-pod-999", "Running", [])
        cs = MockContainerStatus(0, state=MockContainerStateContext(running=MockContainerStateRunning()))
        cs.ready = True
        candidate.status.container_statuses = [cs]
        candidate.metadata.labels = {"app": "sample-app"}
        candidate.metadata.deletion_timestamp = None
        
        mock_v1 = MagicMock()
        mock_v1.list_namespaced_pod.return_value.items = [candidate]
        
        original_timeout = config.RECOVERY_TIMEOUT
        config.RECOVERY_TIMEOUT = 2
        try:
            found = _wait_for_replacement("sample-app", "health-monitor", mock_v1, apps_v1=None, deleted_pod_name="old-pod")
            self.assertTrue(found, "Should declare success when matching deployment pod is Running and Ready")
            self.assertEqual(found, "sample-app-new-pod-999")
        finally:
            config.RECOVERY_TIMEOUT = original_timeout

    def test_attempt_recovery_aborts_when_max_attempts_exceeded(self):
        pod = MockPod("bad-pod-max-retries", "Running", [])
        pod.metadata.namespace = "health-monitor"
        _recovery_tracker["bad-pod-max-retries"]["attempts"] = config.MAX_RECOVERY_ATTEMPTS
        _recovery_tracker["bad-pod-max-retries"]["last_attempt"] = 0.0

        mock_v1 = MagicMock()
        mock_apps = MagicMock()

        attempt_recovery(pod, mock_v1, mock_apps, "CrashLoopBackOff")
        # Deletion must NOT be called when max attempts exceeded
        mock_v1.delete_namespaced_pod.assert_not_called()

    def test_attempt_recovery_skips_controller_pod(self):
        pod = MockPod("health-monitor-controller-xyz", "Running", [])
        pod.metadata.labels = {"app": "health-monitor"}
        mock_v1 = MagicMock()
        mock_apps = MagicMock()

        attempt_recovery(pod, mock_v1, mock_apps, "CrashLoopBackOff")
        mock_v1.delete_namespaced_pod.assert_not_called()

if __name__ == '__main__':
    unittest.main()
