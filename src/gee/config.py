"""Configuration for Google Earth Engine data extraction."""
import os
from pathlib import Path
from typing import Dict, Any, Optional
import ee

# Base directories
BASE_DIR = Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / 'data'
RAW_DATA_DIR = DATA_DIR / 'raw'
PROCESSED_DATA_DIR = DATA_DIR / 'processed'

# Create directories if they don't exist
for directory in [RAW_DATA_DIR, PROCESSED_DATA_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# GEE Authentication
def initialize_gee(service_account: Optional[str] = None, key_file: Optional[str] = None) -> None:
    """Initialize Google Earth Engine with service account credentials.
    
    Args:
        service_account: Service account email (if using service account)
        key_file: Path to service account key file (if using service account)
    """
    try:
        if service_account and key_file:
            credentials = ee.ServiceAccountCredentials(service_account, key_file)
            ee.Initialize(credentials)
        else:
            ee.Initialize()
        print("[SUCCESS] GEE initialized successfully")
    except Exception as e:
        print("[ERROR] GEE initialization failed. Please authenticate using:")
        print("  1. Run: earthengine authenticate")
        print("  2. Follow the instructions to authenticate")
        raise

# Default parameters for data extraction
DEFAULT_PARAMS = {
    'scale': 10,  # meters per pixel
    'crs': 'EPSG:4326',  # Coordinate Reference System
    'maxPixels': 1e13,  # Maximum number of pixels to process
    'bestEffort': True,  # Continue even if computation is large
}

# Study area (Zamboanga City by default, can be overridden)
STUDY_AREA = {
    'name': 'Zamboanga City',
    'coordinates': [
        [122.45, 6.95],  # SW corner
        [122.45, 7.25],  # NW corner
        [122.7, 7.25],   # NE corner
        [122.7, 6.95],   # SE corner
        [122.45, 6.95]   # Close the polygon
    ]
}

def get_study_area() -> ee.Geometry:
    """Get the study area as an Earth Engine Geometry."""
    return ee.Geometry.Polygon(STUDY_AREA['coordinates'], None, False)

# Export settings
EXPORT_PARAMS = {
    'driveFolder': 'povMap_exports',  # Google Drive folder for exports
    'fileFormat': 'GeoTIFF',  # Export format
    'skipEmptyTiles': True,  # Skip empty tiles
    'maxPixels': 1e13,  # Maximum pixels per export
}

# Logging configuration
LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': str(BASE_DIR / 'logs' / 'pipeline.log'),
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
}
