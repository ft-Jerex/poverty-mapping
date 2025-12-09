#!/usr/bin/env python3
"""
Investigate why the API returns 2673 cells instead of expected 1724.
"""

import geopandas as gpd
import pandas as pd
import json
from pathlib import Path

# File paths from app.py
ROOT = Path(".")
DATA_DIR = ROOT / "data"
GRID_GEOJSON_PATH = DATA_DIR / "grid_with_comprehensive_data.geojson"
MERGED_PREDICTIONS_PATH = DATA_DIR / "grid_predictions_comparison.csv"

print("=== Investigating API Data Source ===")

# Load the exact files the API uses
print("Loading files used by API...")
grid_gdf = gpd.read_file(GRID_GEOJSON_PATH)
merged_df = pd.read_csv(MERGED_PREDICTIONS_PATH)

print(f"GeoJSON file: {len(grid_gdf)} features")
print(f"Predictions file: {len(merged_df)} rows")

# Perform the same merge as the API
print("\nPerforming merge as in API...")
gdf = grid_gdf.merge(merged_df, on="grid_id", how="left")
print(f"After merge: {len(gdf)} rows")

# Check for NaN predictions
valid_cat = gdf["pred_scaled_catboost"].notna()
valid_rf = gdf["pred_scaled_rf"].notna()

print(f"Valid CatBoost predictions: {valid_cat.sum()}")
print(f"Valid RF predictions: {valid_rf.sum()}")
print(f"Invalid CatBoost predictions: {(~valid_cat).sum()}")

# This should match what the API returns
print(f"\nExpected API CatBoost features: {valid_cat.sum()}")
print(f"Expected API RF features: {valid_rf.sum()}")

# Check if there are duplicate grid_ids in the GeoJSON that might inflate the count
duplicates = grid_gdf['grid_id'].duplicated().sum()
print(f"Duplicate grid_ids in GeoJSON: {duplicates}")

# Check unique grid_ids
unique_geojson = len(grid_gdf['grid_id'].unique())
unique_predictions = len(merged_df['grid_id'].unique())
print(f"Unique grid_ids in GeoJSON: {unique_geojson}")
print(f"Unique grid_ids in predictions: {unique_predictions}")

# If the API shows 2673, there must be additional processing
# Check the CNN path to see if it adds more data
CNN_PRED_PATH = DATA_DIR / "all_cells_predictions_1km.csv"
GRID_GPKG_PATH = DATA_DIR / "grid_1km_all.gpkg"

if GRID_GPKG_PATH.exists():
    print(f"\nChecking CNN grid file...")
    try:
        grid_gdf_cnn = gpd.read_file(GRID_GPKG_PATH)
        print(f"CNN grid file: {len(grid_gdf_cnn)} features")
        
        # This might be the source of the larger count
        if len(grid_gdf_cnn) > len(grid_gdf):
            print("*** CNN grid file is larger than main grid file! ***")
            print("This could explain the 2673 count in the API.")
    except Exception as e:
        print(f"Error reading CNN grid file: {e}")

print("\n=== Analysis Complete ===")
print("If API returns 2673 cells, it suggests either:")
print("1. Different grid file is being used for some models")
print("2. Data is being combined from multiple sources")
print("3. There's additional processing not visible in this analysis")