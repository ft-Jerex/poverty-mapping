#!/bin/bash
# Convert Google Earth Engine service account JSON to environment variable

set -e

JSON_FILE="${1:-env/ee-zc-povertymapping-0c4c39483d32.json}"
ENV_FILE="${2:-.env.production}"

if [ ! -f "$JSON_FILE" ]; then
    echo "❌ JSON file not found: $JSON_FILE"
    echo "Usage: $0 [json-file] [env-file]"
    exit 1
fi

echo "🔑 Converting $JSON_FILE to environment variable..."

# Minify JSON (remove whitespace and newlines)
JSON_CONTENT=$(cat "$JSON_FILE" | jq -c .)

# Create or update .env file
cat > "$ENV_FILE" << EOF
# Flask Configuration
FLASK_SECRET_KEY=$(openssl rand -hex 32)
FLASK_ENV=production

# Google Earth Engine Configuration  
GEE_PROJECT_ID=ee-zc-povertymapping
GEE_SERVICE_ACCOUNT_JSON='$JSON_CONTENT'

# Application Settings
APP_PORT=8000
APP_HOST=0.0.0.0

# Database
DATABASE_PATH=/app/data/users.db
EOF

echo "✅ Environment file created: $ENV_FILE"
echo ""
echo "🔒 Security recommendations:"
echo "   1. Add $ENV_FILE to .gitignore"
echo "   2. Remove the original JSON file from git:"
echo "      git rm --cached $JSON_FILE"
echo "   3. Deploy with: docker-compose --env-file $ENV_FILE up -d"
echo ""
echo "⚠️  Keep $ENV_FILE secure and never commit it to version control!"