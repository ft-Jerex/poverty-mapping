#!/usr/bin/env python3
"""
Investigate the complete grid coverage issue by examining all prediction sources.
"""

import pandas as pd
from pathlib import Path

print("=== Complete Grid Coverage Investigation ===")

# Check the separate labeled/unlabeled predictions
labeled_cat = Path("data/catBoost/geospatial_disagg/grid_predictions.csv")
unlabeled_cat = Path("data/catBoost/geospatial_disagg/unlabeled_grid_predictions.csv")
labeled_rf = Path("data/rf/geospatial_disagg/grid_predictions.csv") 
unlabeled_rf = Path("data/rf/geospatial_disagg/unlabeled_grid_predictions.csv")

current_merged = Path("data/grid_predictions_comparison.csv")
grid_geojson_data = Path("data/grid_with_comprehensive_data.geojson")

print("\n1. Separate prediction files:")
if labeled_cat.exists():
    df_lab_cat = pd.read_csv(labeled_cat)
    print(f"Labeled CatBoost: {len(df_lab_cat)} rows")
    print(f"  Sample grid_ids: {df_lab_cat['grid_id'].head(3).tolist()}")

if unlabeled_cat.exists():
    df_unlab_cat = pd.read_csv(unlabeled_cat)
    print(f"Unlabeled CatBoost: {len(df_unlab_cat)} rows")
    print(f"  Sample grid_ids: {df_unlab_cat['grid_id'].head(3).tolist()}")

if labeled_rf.exists():
    df_lab_rf = pd.read_csv(labeled_rf)
    print(f"Labeled RF: {len(df_lab_rf)} rows")

if unlabeled_rf.exists():
    df_unlab_rf = pd.read_csv(unlabeled_rf)
    print(f"Unlabeled RF: {len(df_unlab_rf)} rows")

print(f"\n2. Current merged file:")
if current_merged.exists():
    df_merged = pd.read_csv(current_merged)
    print(f"Current merged: {len(df_merged)} rows")
    print(f"Unique grid_ids: {df_merged['grid_id'].nunique()}")

# Check what's the actual grid coverage that should be expected
print(f"\n3. Expected total coverage:")
expected_total = 0
if labeled_cat.exists() and unlabeled_cat.exists():
    combined_grid_ids = set(df_lab_cat['grid_id']) | set(df_unlab_cat['grid_id'])
    expected_total = len(combined_grid_ids)
    print(f"Expected total (labeled + unlabeled): {expected_total} unique grid cells")
    
    # Check overlap
    overlap = set(df_lab_cat['grid_id']) & set(df_unlab_cat['grid_id'])
    print(f"Overlap between labeled/unlabeled: {len(overlap)} cells")

# Check the large grid file
large_grid = Path("assets/grid_with_comprehensive_data.csv")
if large_grid.exists():
    df_large = pd.read_csv(large_grid)
    unique_large = df_large['grid_id'].nunique()
    print(f"Large grid file unique cells: {unique_large}")
    
    # Check if this covers more area than current predictions
    if expected_total > 0:
        print(f"Coverage gap: {unique_large - expected_total} cells without predictions")

# Check GEE export for full coverage
gee_export = Path("googleEarthExports/zc04_grid_data_2024.csv")
if gee_export.exists():
    df_gee = pd.read_csv(gee_export)
    if 'grid_id' in df_gee.columns:
        unique_gee = df_gee['grid_id'].nunique()
        print(f"GEE export unique cells: {unique_gee}")
    elif 'cell_id' in df_gee.columns:
        unique_gee = df_gee['cell_id'].nunique()
        print(f"GEE export unique cells (cell_id): {unique_gee}")

print(f"\n=== Analysis ===")
print("The issue is likely that:")
print("1. Only 1274 cells have CatBoost/RF predictions (labeled + unlabeled)")
print("2. But the full grid should cover ~1700+ or even 11,000+ cells")
print("3. The missing areas are cells without any predictions at all")
print("4. These gaps appear as blank areas in the visualization")

print(f"\nTo fix complete coverage, need to:")
print("1. Generate predictions for ALL grid cells in the ROI")
print("2. Combine labeled + unlabeled + missing area predictions") 
print("3. Ensure spatial coverage matches the full administrative boundary")