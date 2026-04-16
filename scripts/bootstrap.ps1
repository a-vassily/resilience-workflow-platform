param(
    [switch]$RebuildDb = $false
)

Write-Host "=== Resilience Prototype Bootstrap ===" -ForegroundColor Cyan

# 1. Check Docker
try {
    docker version | Out-Null
    Write-Host "[OK] Docker is available" -ForegroundColor Green
}
catch {
    Write-Host "[FAIL] Docker Desktop is not running or not installed." -ForegroundColor Red
    exit 1
}

# 2. Start infrastructure
if ($RebuildDb) {
    Write-Host "Rebuilding containers and volumes..." -ForegroundColor Yellow
    docker compose down -v
}
else {
    Write-Host "Starting infrastructure containers..." -ForegroundColor Yellow
}

docker compose up -d

if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] docker compose up failed." -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Infrastructure started" -ForegroundColor Green

# 3. Wait a bit for PostgreSQL and MinIO
Write-Host "Waiting for services to initialize..." -ForegroundColor Yellow
Start-Sleep -Seconds 8

# 4. Create Python virtual environment if missing
if (!(Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Virtual environment created" -ForegroundColor Green
}

# 5. Install dependencies
Write-Host "Installing Python dependencies..." -ForegroundColor Yellow
& python.exe -m pip install --upgrade pip
& python.exe -m pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] pip install failed." -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Python dependencies installed" -ForegroundColor Green

# 6. Initialize DB schema if file exists
if (Test-Path ".\db\init.sql") {
    Write-Host "Applying PostgreSQL schema..." -ForegroundColor Yellow
    Get-Content .\db\init.sql | docker exec -i resilience_postgres psql -U resilience -d resilience
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Database initialization failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Database schema applied" -ForegroundColor Green
}
else {
    Write-Host "[WARN] db/init.sql not found, skipping DB init." -ForegroundColor Yellow
}

# 7. Setup MinIO buckets
if (Test-Path ".\scripts\setup_minio.py") {
    Write-Host "Creating MinIO buckets..." -ForegroundColor Yellow
    & python.exe .\scripts\setup_minio.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] MinIO bucket setup failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] MinIO buckets ready" -ForegroundColor Green
}
else {
    Write-Host "[WARN] scripts/setup_minio.py not found, skipping bucket setup." -ForegroundColor Yellow
}

# 8. Environment test
if (Test-Path ".\scripts\test_environment.py") {
    Write-Host "Running environment tests..." -ForegroundColor Yellow
    & python.exe .\scripts\test_environment.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] Environment test failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Environment tests passed" -ForegroundColor Green
}
else {
    Write-Host "[WARN] scripts/test_environment.py not found, skipping tests." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Bootstrap complete." -ForegroundColor Cyan
Write-Host "Next step: start the APIs and workers from PyCharm." -ForegroundColor White

