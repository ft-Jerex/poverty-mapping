# PowerShell script to convert GEE JSON to environment variable
param(
    [string]$JsonFile = "env\ee-zc-povertymapping-0c4c39483d32.json",
    [string]$EnvFile = ".env.production"
)

if (-not (Test-Path $JsonFile)) {
    Write-Host "❌ JSON file not found: $JsonFile" -ForegroundColor Red
    Write-Host "Usage: .\convert-gee-json.ps1 [-JsonFile path] [-EnvFile path]"
    exit 1
}

Write-Host "🔑 Converting $JsonFile to environment variable..." -ForegroundColor Yellow

# Read and minify JSON
$jsonContent = Get-Content $JsonFile -Raw | ConvertFrom-Json | ConvertTo-Json -Compress

# Generate secret key
$secretKey = [System.Web.Security.Membership]::GeneratePassword(64, 10)

# Create environment file
@"
# Flask Configuration
FLASK_SECRET_KEY=$secretKey
FLASK_ENV=production

# Google Earth Engine Configuration  
GEE_PROJECT_ID=ee-zc-povertymapping
GEE_SERVICE_ACCOUNT_JSON='$jsonContent'

# Application Settings
APP_PORT=8000
APP_HOST=0.0.0.0

# Database
DATABASE_PATH=/app/data/users.db
"@ | Out-File -FilePath $EnvFile -Encoding UTF8

Write-Host "✅ Environment file created: $EnvFile" -ForegroundColor Green
Write-Host ""
Write-Host "🔒 Security recommendations:" -ForegroundColor Cyan
Write-Host "   1. Add $EnvFile to .gitignore"
Write-Host "   2. Remove the original JSON file from git:"
Write-Host "      git rm --cached $JsonFile"  
Write-Host "   3. Deploy with: docker-compose --env-file $EnvFile up -d"
Write-Host ""
Write-Host "⚠️  Keep $EnvFile secure and never commit it to version control!" -ForegroundColor Red