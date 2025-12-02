# Deployment Guide for Poverty Mapping Application

## Quick Deploy (Recommended)

### Option 1: Docker Deployment (Linux/Mac)
```bash
# Make deploy script executable
chmod +x deploy.sh

# Run deployment
./deploy.sh
```

### Option 2: Docker Deployment (Windows)
```powershell
# Run deployment script
.\deploy.ps1
```

### Option 3: Manual Docker Deployment
```bash
# 1. Create environment file
cp .env.example .env.production
# Edit .env.production with your settings

# 2. Build and start
docker-compose --env-file .env.production up -d --build

# 3. Check health
curl http://localhost:8000/health
```

## Deployment Options

### 1. Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export FLASK_SECRET_KEY="your-secret-key"

# Run application
python -m src.server.app
```
**Access:** http://localhost:8000

### 2. Docker (Single Container)
```bash
# Build image
docker build -t poverty-mapping .

# Run container
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -e FLASK_SECRET_KEY="your-secret-key" \
  --name poverty-mapping \
  poverty-mapping
```

### 3. Docker Compose (Recommended)
```bash
# Simple deployment
docker-compose up -d

# With custom environment
docker-compose --env-file .env.production up -d
```

### 4. Cloud Deployment

#### AWS EC2
```bash
# 1. Launch EC2 instance (Ubuntu 20.04+)
# 2. Install Docker & Docker Compose
sudo apt update
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker $USER

# 3. Clone and deploy
git clone <your-repo>
cd poverty-mapping-withbackend
./deploy.sh
```

#### Google Cloud Run
```bash
# 1. Build and push to Container Registry
gcloud builds submit --tag gcr.io/PROJECT_ID/poverty-mapping

# 2. Deploy to Cloud Run
gcloud run deploy poverty-mapping \
  --image gcr.io/PROJECT_ID/poverty-mapping \
  --platform managed \
  --port 8000 \
  --set-env-vars FLASK_SECRET_KEY="your-secret-key"
```

#### DigitalOcean Droplet
```bash
# 1. Create Droplet with Docker
# 2. Clone repository
git clone <your-repo>
cd poverty-mapping-withbackend

# 3. Deploy
./deploy.sh
```

## Configuration

### Environment Variables
Create `.env.production`:
```bash
# Required
FLASK_SECRET_KEY=your-random-secret-key-here
FLASK_ENV=production

# Optional - Google Earth Engine
GEE_CREDENTIALS_PATH=/path/to/service-account.json

# Optional - Custom settings
APP_PORT=8000
APP_HOST=0.0.0.0
```

### Google Earth Engine Setup (Optional)
Only needed for data updates:
```bash
# 1. Create service account in Google Cloud Console
# 2. Download JSON key file
# 3. Set GEE_CREDENTIALS_PATH in environment
# 4. Register service account with Earth Engine
```

## Production Considerations

### 1. Security
- Use strong `FLASK_SECRET_KEY`
- Configure firewall (only allow ports 80, 443, 22)
- Set up SSL certificates (Let's Encrypt)
- Use reverse proxy (nginx)

### 2. Data Backup
```bash
# Automated backup script
#!/bin/bash
timestamp=$(date +%Y%m%d_%H%M%S)
tar -czf "backup_$timestamp.tar.gz" data/
aws s3 cp "backup_$timestamp.tar.gz" s3://your-backup-bucket/
```

### 3. Monitoring
- Health check endpoint: `/health`
- Application logs: `docker-compose logs -f`
- System monitoring: Prometheus + Grafana

### 4. Updates
```bash
# Update application
git pull
docker-compose up -d --build

# With zero downtime (blue-green)
docker-compose -f docker-compose.blue.yml up -d
# Switch traffic, then update green
```

## SSL Setup with Let's Encrypt

```bash
# 1. Install Certbot
sudo apt install certbot python3-certbot-nginx

# 2. Get certificates
sudo certbot --nginx -d your-domain.com

# 3. Update nginx.conf with SSL settings
# 4. Restart services
docker-compose restart nginx
```

## Troubleshooting

### Common Issues

1. **Port already in use**
   ```bash
   # Find process using port 8000
   sudo lsof -i :8000
   # Kill process or change port in docker-compose.yml
   ```

2. **Permission denied**
   ```bash
   # Fix file permissions
   sudo chown -R $USER:$USER ./data
   chmod 755 ./data
   ```

3. **Container fails to start**
   ```bash
   # Check logs
   docker-compose logs poverty-mapping
   # Check disk space
   df -h
   ```

4. **Health check fails**
   ```bash
   # Check if application is running
   docker-compose ps
   # Test health endpoint
   curl -v http://localhost:8000/health
   ```

## Performance Tuning

### 1. Database Optimization
- Use SQLite WAL mode for better concurrency
- Regular VACUUM operations
- Index optimization

### 2. Caching
- Enable nginx gzip compression
- Set appropriate cache headers
- Use CDN for static assets

### 3. Resource Limits
```yaml
# In docker-compose.yml
services:
  poverty-mapping:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          memory: 2G
```

## Monitoring & Maintenance

### Health Monitoring
```bash
# Simple uptime monitoring
*/5 * * * * curl -f http://localhost:8000/health || echo "App down" | mail admin@domain.com
```

### Log Rotation
```bash
# Configure Docker log rotation in daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

### Backup Strategy
- Daily data backups
- Weekly full system backups
- Test restore procedures monthly
- Store backups off-site (AWS S3, Google Cloud Storage)

## Scaling

### Horizontal Scaling
```yaml
# docker-compose.yml for multiple instances
services:
  poverty-mapping:
    deploy:
      replicas: 3
  
  load-balancer:
    image: nginx
    # Configure load balancing
```

### Database Scaling
- Consider PostgreSQL for high-traffic deployments
- Read replicas for analytics queries
- Connection pooling

---

**Need Help?**
- Check logs: `docker-compose logs -f`
- Health check: `curl http://localhost:8000/health`
- Issues: Create GitHub issue with logs and environment details