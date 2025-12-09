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

# Copy application code
COPY src/ ./src/
COPY models/ ./models/
COPY static/ ./static/
COPY web/ ./web/
COPY assets/ ./assets/

# Copy optional users.db if it exists
RUN if [ -f users.db ]; then cp users.db ./users.db; fi

# Create necessary directories and set permissions
RUN mkdir -p data output assets googleEarthExports csv_outputs && \
    chmod -R 755 data output assets googleEarthExports csv_outputs

# Expose port
EXPOSE 8000

# Set environment variables
ENV FLASK_ENV=production
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["python", "-m", "src.server.app"]