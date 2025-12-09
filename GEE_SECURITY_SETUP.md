# ========================================
# SECURE SETUP FOR GOOGLE EARTH ENGINE
# ========================================

# IMPORTANT: Do NOT commit service account JSON files to git!
# Use environment variables instead for better security.

## Method 1: Environment Variable (Recommended for production)

# 1. Copy your service account JSON content
# 2. Create .env.production file:

FLASK_SECRET_KEY=your-super-secret-key-here
GEE_PROJECT_ID=ee-zc-povertymapping
GEE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"ee-zc-povertymapping",...}

# 3. Deploy with:
docker-compose --env-file .env.production up -d

## Method 2: File Path (Local development)

# 1. Place JSON file outside git repo:
mkdir -p ~/.config/gee
cp ee-zc-povertymapping-0c4c39483d32.json ~/.config/gee/

# 2. Set path in environment:
GEE_CREDENTIALS_PATH=/home/user/.config/gee/ee-zc-povertymapping-0c4c39483d32.json

## Method 3: Cloud Provider Secrets

### AWS Secrets Manager
aws secretsmanager create-secret --name "gee-service-account" --secret-string file://service-account.json

### Google Cloud Secret Manager  
gcloud secrets create gee-service-account --data-file=service-account.json

### Azure Key Vault
az keyvault secret set --vault-name MyKeyVault --name gee-service-account --file service-account.json

# ========================================
# SECURITY BEST PRACTICES
# ========================================

# 1. Add to .gitignore:
echo "*.json" >> .gitignore
echo ".env*" >> .gitignore
echo "!.env.example" >> .gitignore

# 2. Remove any committed JSON files:
git rm --cached env/ee-zc-povertymapping-0c4c39483d32.json
git commit -m "Remove service account key from repo"

# 3. Use different service accounts for dev/prod
# 4. Rotate keys regularly  
# 5. Limit service account permissions to minimum required

# ========================================
# DEPLOYMENT EXAMPLES
# ========================================

## Local Docker
docker run -d \
  -p 8000:8000 \
  -e FLASK_SECRET_KEY="random-secret" \
  -e GEE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}' \
  poverty-mapping

## Cloud Run
gcloud run deploy poverty-mapping \
  --image gcr.io/PROJECT/poverty-mapping \
  --set-env-vars FLASK_SECRET_KEY="secret" \
  --set-env-vars GEE_PROJECT_ID="ee-zc-povertymapping" \
  --set-secrets GEE_SERVICE_ACCOUNT_JSON=gee-service-account:latest

## Kubernetes
kubectl create secret generic gee-credentials \
  --from-file=service-account.json=ee-zc-povertymapping-0c4c39483d32.json
  
# Then reference in deployment yaml:
env:
- name: GEE_SERVICE_ACCOUNT_JSON  
  valueFrom:
    secretKeyRef:
      name: gee-credentials
      key: service-account.json