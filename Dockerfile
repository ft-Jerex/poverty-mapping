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

# Copy config files
COPY requirements.txt ./

# Create necessary directories and set permissions
RUN mkdir -p data && chmod -R 755 data

# Copy users.db if it exists (for pre-seeded users)
COPY users.db ./data/users.db

# Copy required webapp data files to data directory
# These are needed for the webapp to display predictions immediately
RUN cp -r assets/shapefile data/shapefile 2>/dev/null || true && \
    cp output/grids/grid_1km.gpkg data/grid_1km_all.gpkg 2>/dev/null || true && \
    cp output/grid_predictions_comparison.csv data/complete_grid_predictions.csv 2>/dev/null || true && \
    cp output/cnn_reuse_1km_2024/grid_predictions_2024_filled.csv data/all_cells_predictions_1km.csv 2>/dev/null || true

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