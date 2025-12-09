#!/usr/bin/env python3
"""
Investigate spatial gaps in predictions - why certain areas don't render.
"""

import geopandas as gpd
import pandas as pd
import numpy as np
from pathlib import Path

# File paths
SHAPEFILE_PATH = Path("data/shapefile/zc04AdminBoundaries_gcs.shp")
GRID_GEOJSON_PATH = Path("data/grid_with_comprehensive_data.geojson")
MERGED_PREDICTIONS_PATH = Path("data/grid_predictions_comparison.csv")

print("=== Investigating Spatial Prediction Gaps ===")

# Load the data
print("Loading spatial data...")
roi_gdf = gpd.read_file(SHAPEFILE_PATH)
grid_gdf = gpd.read_file(GRID_GEOJSON_PATH)
predictions_df = pd.read_csv(MERGED_PREDICTIONS_PATH)

print(f"ROI boundaries: {len(roi_gdf)} features")
print(f"Grid cells: {len(grid_gdf)} features")
print(f"Predictions: {len(predictions_df)} rows")

# Check CRS consistency
print(f"\nCRS Information:")
print(f"ROI CRS: {roi_gdf.crs}")
print(f"Grid CRS: {grid_gdf.crs}")

# Merge predictions with grid
merged_gdf = grid_gdf.merge(predictions_df, on="grid_id", how="left")
print(f"After merge: {len(merged_gdf)} grid cells")

# Check spatial coverage
print(f"\nSpatial Analysis:")
print(f"Grid bounds: {grid_gdf.total_bounds}")
print(f"ROI bounds: {roi_gdf.total_bounds}")

# Identify cells with and without predictions
has_predictions = merged_gdf["pred_scaled_catboost"].notna()
no_predictions = ~has_predictions

print(f"\nPrediction Coverage:")
print(f"Cells with predictions: {has_predictions.sum()}")
print(f"Cells without predictions: {no_predictions.sum()}")

# Analyze cells without predictions
if no_predictions.any():
    print(f"\nAnalyzing cells without predictions...")
    no_pred_cells = merged_gdf[no_predictions]
    
    # Check if they're inside ROI boundaries
    if roi_gdf.crs != grid_gdf.crs:
        roi_gdf = roi_gdf.to_crs(grid_gdf.crs)
    
    roi_union = roi_gdf.geometry.unary_union
    
    # Spatial join to see which cells are inside/outside ROI
    inside_roi = no_pred_cells.geometry.intersects(roi_union)
    
    print(f"Cells without predictions inside ROI: {inside_roi.sum()}")
    print(f"Cells without predictions outside ROI: {(~inside_roi).sum()}")
    
    if inside_roi.any():
        print("\nSample cells inside ROI without predictions:")
        sample_inside = no_pred_cells[inside_roi].head(5)
        for _, cell in sample_inside.iterrows():
            print(f"  grid_id: {cell['grid_id']}, barangay: {cell.get('barangay_name_clean', 'N/A')}")

# Check for specific geographical patterns
print(f"\nChecking specific area types...")

# Check if there are barangay name patterns
if 'barangay_name_clean' in merged_gdf.columns:
    # Group by barangay and check prediction coverage
    brgy_stats = merged_gdf.groupby('barangay_name_clean').agg({
        'pred_scaled_catboost': ['count', lambda x: x.notna().sum()]
    }).round(2)
    brgy_stats.columns = ['total_cells', 'cells_with_predictions']
    brgy_stats['coverage_pct'] = (brgy_stats['cells_with_predictions'] / brgy_stats['total_cells'] * 100).round(1)
    
    # Find barangays with poor coverage
    poor_coverage = brgy_stats[brgy_stats['coverage_pct'] < 100].sort_values('coverage_pct')
    
    if not poor_coverage.empty:
        print(f"\nBarangays with incomplete prediction coverage:")
        print(poor_coverage.head(10).to_string())

# Check for specific area characteristics that might cause exclusion
print(f"\nChecking area characteristics...")

# Check protected areas
if 'is_protected_forest' in merged_gdf.columns:
    protected = merged_gdf['is_protected_forest'] == 1
    print(f"Protected forest cells: {protected.sum()}")
    if protected.any():
        protected_with_pred = merged_gdf[protected]['pred_scaled_catboost'].notna().sum()
        print(f"Protected forest cells with predictions: {protected_with_pred}")

# Check islands
if 'is_island' in merged_gdf.columns:
    islands = merged_gdf['is_island'] == 1
    print(f"Island cells: {islands.sum()}")
    if islands.any():
        islands_with_pred = merged_gdf[islands]['pred_scaled_catboost'].notna().sum()
        print(f"Island cells with predictions: {islands_with_pred}")

# Check population density
if 'population' in merged_gdf.columns:
    pop_stats = merged_gdf['population'].describe()
    print(f"\nPopulation statistics:")
    print(f"Min: {pop_stats['min']:.2f}, Max: {pop_stats['max']:.2f}, Mean: {pop_stats['mean']:.2f}")
    
    # Check if low/zero population areas have fewer predictions
    zero_pop = merged_gdf['population'] == 0
    low_pop = (merged_gdf['population'] > 0) & (merged_gdf['population'] < 1)
    
    if zero_pop.any():
        zero_pop_pred_rate = merged_gdf[zero_pop]['pred_scaled_catboost'].notna().mean()
        print(f"Zero population cells prediction rate: {zero_pop_pred_rate:.2%}")
    
    if low_pop.any():
        low_pop_pred_rate = merged_gdf[low_pop]['pred_scaled_catboost'].notna().mean()
        print(f"Low population (<1) cells prediction rate: {low_pop_pred_rate:.2%}")

print(f"\n=== Recommendations ===")
print("1. Check if missing predictions are in specific geographic areas")
print("2. Verify if model training excluded certain area types")
print("3. Consider if administrative boundaries are filtering out cells")
print("4. Check if data quality filters are too restrictive")