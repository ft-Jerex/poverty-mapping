#!/bin/bash

# Production deployment script for Poverty Mapping Application

set -e  # Exit on any error

echo "🚀 Starting deployment of Poverty Mapping Application..."

# Configuration
APP_NAME="poverty-mapping"
BACKUP_DIR="./backups"
ENV_FILE=".env.production"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# 1. Environment Setup
echo "📋 Setting up environment..."
if [ ! -f "$ENV_FILE" ]; then
    echo "⚠️  Creating default environment file at $ENV_FILE"
    cat > "$ENV_FILE" << EOF
# Flask Configuration
FLASK_SECRET_KEY=$(openssl rand -hex 32)
FLASK_ENV=production

# Google Earth Engine (optional - for data updates)
# GEE_CREDENTIALS_PATH=/path/to/gee-service-account.json

# Application Settings
APP_PORT=8000
APP_HOST=0.0.0.0

# Database
DATABASE_PATH=/app/data/users.db
EOF
    echo "✅ Please edit $ENV_FILE with your configuration"
fi

# 2. Backup existing data
if [ -d "./data" ]; then
    echo "💾 Backing up existing data..."
    timestamp=$(date +%Y%m%d_%H%M%S)
    tar -czf "$BACKUP_DIR/data_backup_$timestamp.tar.gz" ./data
    echo "✅ Data backed up to $BACKUP_DIR/data_backup_$timestamp.tar.gz"
fi

# 3. Build and deploy with Docker
echo "🔨 Building Docker image..."
docker-compose --env-file "$ENV_FILE" build

echo "🚀 Starting services..."
docker-compose --env-file "$ENV_FILE" up -d

# 4. Wait for health check
echo "🏥 Waiting for application to be healthy..."
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -f http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Application is healthy!"
        break
    fi
    echo "⏳ Waiting for application to start... (attempt $((attempt+1))/$max_attempts)"
    sleep 10
    attempt=$((attempt+1))
done

if [ $attempt -eq $max_attempts ]; then
    echo "❌ Application failed to become healthy. Check logs:"
    docker-compose logs
    exit 1
fi

# 5. Display deployment info
echo ""
echo "🎉 Deployment completed successfully!"
echo ""
echo "📊 Application URLs:"
echo "   • Main App: http://localhost:8000"
echo "   • Health Check: http://localhost:8000/health"
echo "   • Admin Panel: http://localhost:8000/admin"
echo ""
echo "🔧 Management Commands:"
echo "   • View logs: docker-compose logs -f"
echo "   • Stop app: docker-compose down"
echo "   • Restart: docker-compose restart"
echo "   • Update: git pull && docker-compose up -d --build"
echo ""
echo "📁 Data locations:"
echo "   • Database: ./data/users.db"
echo "   • Predictions: ./data/*.csv"
echo "   • Backups: $BACKUP_DIR"
echo ""

# 6. Show status
docker-compose ps