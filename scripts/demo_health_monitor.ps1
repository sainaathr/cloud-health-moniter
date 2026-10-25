# ==============================================================================
# Auto Kubernetes Health Monitoring & Self-Healing - Interactive Demo CLI
# ==============================================================================
# This PowerShell script allows you to easily demonstrate:
#   1. Complete Unit & Safety Test Suite (Controller, Persistence, UI)
#   2. Local Web Control Plane (Dark DevOps Dashboard)
#   3. Live Minikube Cluster Diagnostics
#   4. Controlled HTTP Traffic Generator (HPA Trigger on live K8s Service or local UI)
#   5. SQLite Incident History & Audit Logs
#   6. Grafana Dashboard Update & Push
# ==============================================================================

$Host.UI.RawUI.WindowTitle = "K8s Health Monitor Demo Plane"
if (-not $PSScriptRoot) {
    $RepoRoot = (Get-Location).Path
} else {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

function Show-Header {
    Clear-Host
    Write-Host "==============================================================================" -ForegroundColor Cyan
    Write-Host "      ⎈  Auto Kubernetes Health Monitoring & Self-Healing System  ⎈        " -ForegroundColor Green
    Write-Host "                 Hardened Controller • Persistent Observability               " -ForegroundColor DarkCyan
    Write-Host "==============================================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Show-Menu {
    Show-Header
    Write-Host "Select a demonstration workflow:" -ForegroundColor Yellow
    Write-Host "  [1] Run All Unit & Safety Tests (40 automated test cases)" -ForegroundColor White
    Write-Host "  [2] Start Web Dashboard locally (http://localhost:5000)" -ForegroundColor White
    Write-Host "  [3] Check Live Kubernetes / Minikube Cluster Status" -ForegroundColor White
    Write-Host "  [4] Run Traffic Generator (HPA CPU Scaling Demonstration)" -ForegroundColor White
    Write-Host "  [5] View Persistent Incident History (SQLite incidents.db)" -ForegroundColor White
    Write-Host "  [6] Update Grafana Dashboard Configuration" -ForegroundColor White
    Write-Host "  [7] Check Alerting / SMTP Configuration & Send Safe Test Alert" -ForegroundColor White
    Write-Host "  [8] Run Controlled Readiness Failure & Recovery Demonstration" -ForegroundColor White
    Write-Host "  [9] Exit" -ForegroundColor Gray
    Write-Host ""
}

do {
    Show-Menu
    $choice = Read-Host "Enter your choice [1-9]"

    switch ($choice) {
        "1" {
            Show-Header
            Write-Host "Running comprehensive test discovery suite..." -ForegroundColor Yellow
            Push-Location $RepoRoot
            try {
                py -m unittest discover -s tests -p "test_*.py" -v
            } finally {
                Pop-Location
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "2" {
            Show-Header
            Write-Host "Launching Flask Web Control Plane locally on http://localhost:5000..." -ForegroundColor Green
            Write-Host "Features available:" -ForegroundColor Cyan
            Write-Host "  • Live/Simulated Pods Table" -ForegroundColor White
            Write-Host "  • Safe Bounded CPU and Memory Fault Injections" -ForegroundColor White
            Write-Host "  • Strictly Gated Destructive Chaos Controls (Token & Dry-Run)" -ForegroundColor White
            Write-Host "  • Persistent Incident Timeline (5 Distinct Categories)" -ForegroundColor White
            Write-Host ""
            Write-Host "Press Ctrl+C in this terminal to stop the web server." -ForegroundColor DarkYellow
            Write-Host ""
            Push-Location $RepoRoot
            try {
                $env:FLASK_ENV = "development"
                py -m app.ui.app
            } finally {
                Pop-Location
            }
            Read-Host "Press Enter to return to menu..."
        }

        "3" {
            Show-Header
            Write-Host "Checking local Kubernetes environment (Minikube / Docker)..." -ForegroundColor Yellow
            Write-Host ""
            try {
                Write-Host "--- Minikube Status ---" -ForegroundColor Cyan
                minikube status
            } catch {
                Write-Host "Minikube command error: $_" -ForegroundColor Red
            }

            Write-Host ""
            try {
                Write-Host "--- Cluster Nodes ---" -ForegroundColor Cyan
                kubectl get nodes
                Write-Host ""
                Write-Host "--- Namespace Pods (health-monitor) ---" -ForegroundColor Cyan
                kubectl get pods -n health-monitor -o wide
                Write-Host ""
                Write-Host "--- HPA Status ---" -ForegroundColor Cyan
                kubectl get hpa -n health-monitor
            } catch {
                Write-Host "Kubectl query error: $_" -ForegroundColor Red
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "4" {
            Show-Header
            Write-Host "Controlled Traffic Generator for HPA Scaling Demonstration" -ForegroundColor Yellow
            Write-Host "Options: 'http://127.0.0.1:30080' (K8s Service) or 'http://127.0.0.1:5000' (Local Dashboard)" -ForegroundColor Cyan
            $targetUrl = Read-Host "Enter target URL (Default: http://127.0.0.1:5000)"
            if ([string]::IsNullOrWhiteSpace($targetUrl)) {
                $targetUrl = "http://127.0.0.1:5000"
            }
            $workers = Read-Host "Enter concurrent workers [Default: 5]"
            if ([string]::IsNullOrWhiteSpace($workers)) {
                $workers = 5
            }
            $duration = Read-Host "Enter duration in seconds [Default: 20]"
            if ([string]::IsNullOrWhiteSpace($duration)) {
                $duration = 20
            }

            Push-Location $RepoRoot
            try {
                py scripts/generate_traffic.py --url $targetUrl --workers $workers --duration $duration --complexity 500000
            } finally {
                Pop-Location
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "5" {
            Show-Header
            Write-Host "Reading SQLite Incident Store..." -ForegroundColor Yellow
            Push-Location $RepoRoot
            try {
                py -c "from app.ui.incident_store import get_incidents; incs = get_incidents(15); print(f'Found {len(incs)} incident(s):\n'); [print(f'[{i[\"incident_id\"]}] [{i.get(\"category\",\"GENERAL\")}] {i[\"detection_time\"]} | {i[\"incident_type\"]} | Status: {i[\"status\"]} | Pod: {i[\"target_pod\"]}\n  Action: {i[\"recovery_action\"] or \"None\"}\n  Evidence: {i[\"evidence\"] or \"N/A\"}\n') for i in incs]"
            } catch {
                Write-Host "Error reading incident database: $_" -ForegroundColor Red
            } finally {
                Pop-Location
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "6" {
            Show-Header
            Write-Host "Updating Grafana Dashboard configuration locally and attempting API push..." -ForegroundColor Yellow
            Push-Location $RepoRoot
            try {
                py scripts/update_dash.py
            } finally {
                Pop-Location
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "7" {
            Show-Header
            Write-Host "Inspecting Alerting Configuration & Dispatching Safe Test Notification..." -ForegroundColor Yellow
            Push-Location $RepoRoot
            try {
                py -c "from app.notifications import NotificationConfig, send_test_notification; import json; print('Operational Configuration (Secrets Masked):'); print(json.dumps(NotificationConfig.get_masked_config(), indent=2)); print('\nDispatching safe test alert:'); print(json.dumps(send_test_notification(), indent=2))"
            } catch {
                Write-Host "Notification check error: $_" -ForegroundColor Red
            } finally {
                Pop-Location
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "8" {
            Show-Header
            Write-Host "Running End-to-End Readiness Failure & Autonomous Recovery Sequence..." -ForegroundColor Yellow
            Push-Location $RepoRoot
            try {
                Write-Host "1. Toggling readiness state to False (HTTP 503 probe failure)..." -ForegroundColor Cyan
                py -c "import urllib.request, json; req = urllib.request.Request('http://127.0.0.1:5000/api/chaos/readiness', data=b'{}', headers={'Content-Type': 'application/json'}); res = urllib.request.urlopen(req, timeout=5); print(json.dumps(json.loads(res.read().decode()), indent=2))"
                Write-Host ""
                Write-Host "2. Verifying Service /health/ready returns 503..." -ForegroundColor Cyan
                py -c "import urllib.request; 
try:
    urllib.request.urlopen('http://127.0.0.1:5000/health/ready', timeout=3)
except urllib.error.HTTPError as e:
    print(f'Confirmed: /health/ready returned HTTP {e.code} (Traffic Isolated)')
"
                Write-Host ""
                Write-Host "3. Waiting 3 seconds before restoring readiness..." -ForegroundColor DarkYellow
                Start-Sleep -Seconds 3
                Write-Host "4. Restoring readiness back to True (HTTP 200)..." -ForegroundColor Cyan
                py -c "import urllib.request, json; req = urllib.request.Request('http://127.0.0.1:5000/api/chaos/readiness', data=b'{}', headers={'Content-Type': 'application/json'}); res = urllib.request.urlopen(req, timeout=5); print(json.dumps(json.loads(res.read().decode()), indent=2))"
                Write-Host ""
                Write-Host "5. Checking updated incident store..." -ForegroundColor Green
                py -c "from app.ui.incident_store import get_incidents; incs = get_incidents(3); [print(f'[{i[\"incident_id\"]}] {i[\"detection_time\"]} | {i[\"incident_type\"]} | Status: {i[\"status\"]} | Result: {i[\"recovery_result\"]}') for i in incs]"
            } catch {
                Write-Host "Readiness demo error (ensure dashboard is running on port 5000): $_" -ForegroundColor Red
            } finally {
                Pop-Location
            }
            Write-Host ""
            Read-Host "Press Enter to return to menu..."
        }

        "9" {
            Write-Host "Exiting. Thank you!" -ForegroundColor Green
            break
        }

        default {
            Write-Host "Invalid option. Please choose between 1 and 9." -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
} while ($choice -ne "9")

