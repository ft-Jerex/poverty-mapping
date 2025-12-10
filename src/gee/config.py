"""Configuration for Google Earth Engine data extraction."""
import os
from pathlib import Path
from typing import Dict, Any, Optional
import ee
import json
import base64
from google.oauth2 import service_account

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
    """Initialize Google Earth Engine using env-only credentials when available.

    Priority:
      1) GEE_PRIVATE_KEY_B64 (base64 of full service account JSON)
      2) GEE_PRIVATE_KEY_JSON (raw one-line JSON) or legacy GEE_SERVICE_ACCOUNT_JSON
      3) Optional dev fallback: explicit service_account + key_file
      4) ADC fallback: ee.Initialize()
    """
    scopes = ["https://www.googleapis.com/auth/earthengine"]
    try:
        info = None
        b64 = os.getenv("GEE_PRIVATE_KEY_B64")
        if b64:
            info = json.loads(base64.b64decode(b64))
        else:
            js = os.getenv("GEE_PRIVATE_KEY_JSON") or os.getenv("GEE_SERVICE_ACCOUNT_JSON")
            if js:
                info = json.loads(js)

        if info is not None:
            creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
            project = (
                os.getenv("EE_PROJECT_ID")
                or os.getenv("GEE_PROJECT_ID")
                or info.get("project_id")
            )
            ee.Initialize(creds, project=project)
            print("[SUCCESS] GEE initialized successfully (env)")
            return

        if service_account and key_file:
            credentials = ee.ServiceAccountCredentials(service_account, key_file)
            ee.Initialize(credentials)
            print("[SUCCESS] GEE initialized successfully (file)")
            return

        ee.Initialize()
        print("[SUCCESS] GEE initialized successfully (ADC)")
    except Exception:
        print("[ERROR] GEE initialization failed. Set GEE_PRIVATE_KEY_B64 or GEE_PRIVATE_KEY_JSON in env, or use ADC/dev.")
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
