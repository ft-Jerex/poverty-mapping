# Production deployment for Poverty Mapping Application
# PowerShell version of deploy.sh for Windows

param(
    [string]$EnvFile = ".env.production",
    [string]$BackupDir = "./backups"
)

Write-Host "🚀 Starting deployment of Poverty Mapping Application..." -ForegroundColor Green

# Configuration
$APP_NAME = "poverty-mapping"

# Create backup directory
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# 1. Environment Setup
Write-Host "📋 Setting up environment..." -ForegroundColor Yellow
if (-not (Test-Path $EnvFile)) {
    Write-Host "⚠️  Creating default environment file at $EnvFile" -ForegroundColor Yellow
    
    # Generate random secret key
    $secretKey = [System.Web.Security.Membership]::GeneratePassword(64, 10)
    
    @"
# Flask Configuration
FLASK_SECRET_KEY=$secretKey
FLASK_ENV=production

# Google Earth Engine (optional - for data updates)
# GEE_CREDENTIALS_PATH=C:/path/to/gee-service-account.json

# Application Settings
APP_PORT=8000
APP_HOST=0.0.0.0

# Database
DATABASE_PATH=/app/data/users.db
"@ | Out-File -FilePath $EnvFile -Encoding UTF8
    
    Write-Host "✅ Please edit $EnvFile with your configuration" -ForegroundColor Green
}

# 2. Backup existing data
if (Test-Path "./data") {
    Write-Host "💾 Backing up existing data..." -ForegroundColor Yellow
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupPath = "$BackupDir/data_backup_$timestamp.zip"
    Compress-Archive -Path "./data" -DestinationPath $backupPath
    Write-Host "✅ Data backed up to $backupPath" -ForegroundColor Green
}

# 3. Build and deploy with Docker
Write-Host "🔨 Building Docker image..." -ForegroundColor Yellow
docker-compose --env-file $EnvFile build

Write-Host "🚀 Starting services..." -ForegroundColor Yellow
docker-compose --env-file $EnvFile up -d

# 4. Wait for health check
Write-Host "🏥 Waiting for application to be healthy..." -ForegroundColor Yellow
$maxAttempts = 30
$attempt = 0

while ($attempt -lt $maxAttempts) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            Write-Host "✅ Application is healthy!" -ForegroundColor Green
            break
        }
    }
    catch {
        # Continue trying
    }
    
    $attempt++
    Write-Host "⏳ Waiting for application to start... (attempt $attempt/$maxAttempts)" -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}

if ($attempt -eq $maxAttempts) {
    Write-Host "❌ Application failed to become healthy. Check logs:" -ForegroundColor Red
    docker-compose logs
    exit 1
}

# 5. Display deployment info
Write-Host ""
Write-Host "🎉 Deployment completed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "📊 Application URLs:" -ForegroundColor Cyan
Write-Host "   • Main App: http://localhost:8000"
Write-Host "   • Health Check: http://localhost:8000/health"
Write-Host "   • Admin Panel: http://localhost:8000/admin"
Write-Host ""
Write-Host "🔧 Management Commands:" -ForegroundColor Cyan
Write-Host "   • View logs: docker-compose logs -f"
Write-Host "   • Stop app: docker-compose down"
Write-Host "   • Restart: docker-compose restart"
Write-Host "   • Update: git pull && docker-compose up -d --build"
Write-Host ""
Write-Host "📁 Data locations:" -ForegroundColor Cyan
Write-Host "   • Database: ./data/users.db"
Write-Host "   • Predictions: ./data/*.csv"
Write-Host "   • Backups: $BackupDir"
Write-Host ""

# 6. Show status
docker-compose ps