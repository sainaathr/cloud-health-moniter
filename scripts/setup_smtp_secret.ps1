# ==============================================================================
# Setup SMTP Credentials for Kubernetes Health Monitor
# ==============================================================================
# This script securely prompts for your Gmail SMTP sending address and App Password,
# then creates or updates the Kubernetes Secret 'smtp-credentials' in namespace 'health-monitor'.
#
# Password input is masked using Read-Host -AsSecureString and never echoed or logged.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$Username = "",
    [string]$Recipient = "sainaath.r2025@vitstudent.ac.in",
    [switch]$UpdateLocalEnv
)

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "   ⎈ Kubernetes Health Monitor — Secure SMTP Credential Provisioner            " -ForegroundColor Green
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script configures real Gmail SMTP email delivery to: $Recipient" -ForegroundColor White
Write-Host "For Gmail, you must generate a 16-character App Password from Google Account Security:" -ForegroundColor Yellow
Write-Host "  -> https://myaccount.google.com/apppasswords" -ForegroundColor Gray
Write-Host ""

# 1. Prompt for Sending Gmail Address
if (-not $Username) {
    $Username = Read-Host "Enter your sending Gmail address (e.g. sender@gmail.com)"
}

if (-not $Username -or $Username -notmatch "@") {
    Write-Host "Error: A valid Gmail sending address is required." -ForegroundColor Red
    exit 1
}

# 2. Prompt for App Password securely (masked, not echoed)
Write-Host "Enter your 16-character Gmail App Password (input will be masked): " -NoNewline -ForegroundColor Yellow
$securePass = Read-Host -AsSecureString
$bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass)
$plainPass = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

# Strip spaces in app passwords (Google shows them as 'xxxx xxxx xxxx xxxx')
$cleanPass = $plainPass -replace '\s+', ''

if (-not $cleanPass) {
    Write-Host "Error: App Password cannot be empty." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[1/3] Provisioning Kubernetes Secret 'smtp-credentials' in namespace 'health-monitor'..." -ForegroundColor Cyan

# Ensure namespace exists
$nsCheck = kubectl get namespace health-monitor 2>&1
if ($LASTEXITCODE -ne 0) {
    kubectl create namespace health-monitor | Out-Null
}

# Create or replace secret
$secretYaml = @"
apiVersion: v1
kind: Secret
metadata:
  name: smtp-credentials
  namespace: health-monitor
type: Opaque
stringData:
  smtp-username: "$Username"
  smtp-password: "$cleanPass"
"@

$secretYaml | kubectl apply -f - | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "  -> Successfully updated Kubernetes Secret 'smtp-credentials' (secrets securely masked)." -ForegroundColor Green
} else {
    Write-Host "  -> Error creating Kubernetes secret. Verify cluster connection." -ForegroundColor Red
}

# 3. Optional local .env update
if ($UpdateLocalEnv -or (Test-Path ".env")) {
    Write-Host "[2/3] Updating local .env configuration..." -ForegroundColor Cyan
    $envPath = Join-Path (Get-Location) ".env"
    if (Test-Path $envPath) {
        $content = Get-Content $envPath -Raw
        
        # Replace or add variables
        $replacements = @{
            'SMTP_USERNAME' = $Username
            'SMTP_USER' = $Username
            'SMTP_PASSWORD' = $cleanPass
            'SMTP_FROM_EMAIL' = $Username
            'SMTP_FROM' = $Username
            'SMTP_TO_EMAIL' = $Recipient
            'ALERT_TO_EMAIL' = $Recipient
            'SMTP_DRY_RUN' = 'false'
        }

        foreach ($key in $replacements.Keys) {
            $val = $replacements[$key]
            if ($content -match "(?m)^$key=.*$") {
                $content = $content -replace "(?m)^$key=.*$", "$key=$val"
            } else {
                $content += "`n$key=$val"
            }
        }
        Set-Content -Path $envPath -Value $content -Encoding UTF8
        Write-Host "  -> Local .env updated successfully with sending credentials." -ForegroundColor Green
    }
}

# 4. Roll out restart of pods
Write-Host "[3/3] Triggering rolling restart of deployments to apply credentials..." -ForegroundColor Cyan
kubectl rollout restart deployment/sample-app -n health-monitor | Out-Null
kubectl rollout restart deployment/health-monitor-controller -n health-monitor | Out-Null
Write-Host "  -> Deployments rolled out. Pods will load new SMTP credentials." -ForegroundColor Green

Write-Host ""
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "You can now click 'Demo: Send Real Email' on http://localhost:5000 to verify live delivery to $Recipient." -ForegroundColor White
Write-Host ""
