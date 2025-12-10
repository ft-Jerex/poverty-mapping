"""
Configuration module for the poverty mapping web application.

Centralizes paths and settings so they can be easily modified.
"""
from __future__ import annotations

import os
from pathlib import Path

# Root directory of the webapp
ROOT = Path(__file__).resolve().parents[1]

# Data directories
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
MODELS_DIR = ROOT / "models"

# All required files are inside this workspace
SCRIPTS_DIR = ROOT / "scripts"  # GEE extraction, preprocessing, training scripts
ASSETS_DIR = ROOT / "assets"    # Shapefiles, socioeconomic CSVs, grid data
OUTPUT_DIR = ROOT / "output"    # Model outputs (predictions, feature columns)
GEE_EXPORTS_DIR = ROOT / "googleEarthExports"  # GEE CSV exports

# Database
USERS_DB_PATH = DATA_DIR / "users.db"

# Shapefile for boundaries
SHAPEFILE_PATH = DATA_DIR / "shapefile" / "zc04AdminBoundaries_gcs.shp"

# Prediction output files
GRID_GEOJSON_PATH = DATA_DIR / "grid_with_comprehensive_data.geojson"
MERGED_PREDICTIONS_PATH = DATA_DIR / "grid_predictions_comparison.csv"
GRID_GPKG_PATH = DATA_DIR / "grid_1km_all.gpkg"
CNN_PRED_PATH = DATA_DIR / "all_cells_predictions_1km.csv"

# CSV outputs directory
CSV_DIR = ROOT / "csv_outputs"

# Model paths (trained models)
CATBOOST_MODEL_PATH = MODELS_DIR / "catboost_disagg_model.cbm"
RF_MODEL_PATH = MODELS_DIR / "rf_disagg_model.pkl"
CNN_MODEL_PATH = MODELS_DIR / "pytorch_fusion_cnn" / "final_fusion_model.pth"

# Feature column definitions (from training output)
CATBOOST_FEATURES_PATH = OUTPUT_DIR / "catBoost" / "constrained_disagg" / "feature_columns.json"
RF_FEATURES_PATH = OUTPUT_DIR / "rf" / "constrained_disagg" / "feature_columns.json"

# Refresh settings
REFRESH_COOLDOWN_DAYS = 90  # Warn if refresh less than this many days ago
MAX_DATE_RANGE_DAYS = 365  # Maximum allowed date range for data collection

# Status file for refresh progress
REFRESH_STATUS_PATH = DATA_DIR / "refresh_status.json"


def ensure_directories() -> None:
    """Ensure all required directories exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "shapefile").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "backups").mkdir(parents=True, exist_ok=True)
