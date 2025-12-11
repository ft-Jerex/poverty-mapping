# Multi-stage Dockerfile for Poverty Mapping Application
from python:3.10-slim as base

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    libproj-dev \
    libgeos-dev \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for GDAL
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Copy requirements first (for better Docker layer caching)
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM base AS production

# Copy application code and pipeline components
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY models/ ./models/
COPY static/ ./static/
COPY web/ ./web/
COPY assets/ ./assets/
COPY csv_outputs/ ./csv_outputs/
COPY googleEarthExports/ ./googleEarthExports/
COPY output/ ./output/

# Copy fallback data (committed to repo for guaranteed deployment)
COPY data_fallback/ ./data_fallback/

# Copy config files
COPY requirements.txt ./

# Create data directory and populate with fallback data
# This ensures the webapp works immediately on deployment
RUN mkdir -p data && chmod -R 755 data && \
    cp -r data_fallback/* data/ && \
    echo "Fallback data copied to /app/data"

# Copy users.db if it exists (for pre-seeded users)
COPY users.db ./data/users.db

# Expose port
EXPOSE 8000

# Set environment variables
ENV FLASK_ENV=production
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application with gunicorn (production WSGI server)
# Use 2 workers and 4 threads per worker for better concurrency
# Timeout of 120s to handle long-running status checks during refresh
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", "src.server.app:app"]