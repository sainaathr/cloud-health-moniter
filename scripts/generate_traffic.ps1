param (
    [string]$Url = "",
    [int]$Workers = 5,
    [int]$Duration = 30,
    [int]$Complexity = 500000
)

# Auto-detect URL if not explicitly provided
if ([string]::IsNullOrWhiteSpace($Url)) {
    try {
        $minikubeUrl = minikube service sample-app-service -n health-monitor --url 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($minikubeUrl)) {
            $Url = ($minikubeUrl -split "`r?`n")[0].Trim()
        }
    } catch {
        # Minikube not available or cluster offline
    }
}

if ([string]::IsNullOrWhiteSpace($Url)) {
    $Url = "http://127.0.0.1:30080"
}

Write-Host "Starting traffic generator -> $Url" -ForegroundColor Cyan
Write-Host "Workers: $Workers, Duration: $Duration`s, CPU Complexity: $Complexity" -ForegroundColor Gray

# Pre-flight check
try {
    $res = Invoke-WebRequest -Uri "$Url/health/live" -UseBasicParsing -TimeoutSec 3
    if ($res.StatusCode -ne 200) {
        Write-Host "Warning: /health/live did not return 200 OK. Application might be down." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Error connecting to target URL ($Url): $_" -ForegroundColor Red
    Write-Host "Tip: Run 'minikube service sample-app-service -n health-monitor --url' or verify the service port forward." -ForegroundColor Yellow
    exit 1
}

$endTime = (Get-Date).AddSeconds($Duration)
$jobs = @()

for ($i = 0; $i -lt $Workers; $i++) {
    $job = Start-Job -ScriptBlock {
        param($targetUrl, $jobDuration, $jobComplexity)
        $stopTime = (Get-Date).AddSeconds($jobDuration)
        $success = 0
        $errors = 0
        while ((Get-Date) -lt $stopTime) {
            try {
                $response = Invoke-WebRequest -Uri "$targetUrl/api/work?complexity=$jobComplexity" -UseBasicParsing -TimeoutSec 5 -ErrorAction SilentlyContinue 
                if ($response.StatusCode -eq 200) { $success++ } else { $errors++ }
            } catch {
                $errors++
            }
            Start-Sleep -Milliseconds 200
        }
        return @{ Success = $success; Errors = $errors }
    } -ArgumentList $Url, $Duration, $Complexity
    $jobs += $job
}

Write-Host "Generation started. Waiting for jobs to complete..."

# Add visual progress indicator
while ($jobs.State -contains 'Running') {
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "=== Traffic Generation Results ===" -ForegroundColor Green
$totalSuccess = 0
$totalErrors = 0

foreach ($job in $jobs) {
    $result = Receive-Job -Job $job
    if ($result) {
        $totalSuccess += $result.Success
        $totalErrors += $result.Errors
    }
    Remove-Job -Job $job
}

Write-Host "Successful Requests: $totalSuccess" -ForegroundColor Green
Write-Host "Failed Requests: $totalErrors" -ForegroundColor Red
