#!/usr/bin/env python3
"""
Export missing cell locations to understand geographic patterns.
"""

import geopandas as gpd
import pandas as pd
from pathlib import Path

# Load data
grid_gdf = gpd.read_file("data/grid_with_comprehensive_data.geojson")
predictions_df = pd.read_csv("data/grid_predictions_comparison.csv")

# Merge and identify missing cells
merged_gdf = grid_gdf.merge(predictions_df, on="grid_id", how="left")
missing_cells = merged_gdf[merged_gdf["pred_scaled_catboost"].isna()].copy()

print(f"=== Missing Prediction Cells Analysis ===")
print(f"Total missing cells: {len(missing_cells)}")

# Check available columns
print(f"\nAvailable columns: {list(missing_cells.columns)}")

# Show details of missing cells
print(f"\nMissing cells details:")
# Use available columns only
available_cols = ['grid_id']
col_mapping = {
    'lon': 'lon_x' if 'lon_x' in missing_cells.columns else 'lon',
    'lat': 'lat_x' if 'lat_x' in missing_cells.columns else 'lat', 
    'barangay': 'barangay_name_clean_x' if 'barangay_name_clean_x' in missing_cells.columns else 'barangay_name_clean'
}

for col in [col_mapping['lon'], col_mapping['lat'], col_mapping['barangay'], 'population', 'is_protected_forest', 'is_island']:
    if col in missing_cells.columns:
        available_cols.append(col)

missing_info = missing_cells[available_cols].copy()

# Add approximate location description based on coordinates
def describe_location(row):
    lon_col = col_mapping['lon']
    lat_col = col_mapping['lat']
    lon, lat = row[lon_col], row[lat_col]
    
    # Zamboanga City rough boundaries
    # Western part: lon < 122.0
    # Eastern part: lon > 122.0  
    # Northern part: lat > 7.2
    # Southern part: lat < 7.2
    
    location = ""
    if lon < 122.0:
        location += "Western "
    elif lon > 122.2:
        location += "Eastern "
    else:
        location += "Central "
        
    if lat > 7.2:
        location += "Northern area"
    elif lat < 7.0:
        location += "Southern area"
    else:
        location += "Central area"
        
    return location

missing_info['location_desc'] = missing_info.apply(describe_location, axis=1)

print(missing_info.to_string(index=False))

# Group by location characteristics
print(f"\n=== Geographic Distribution of Missing Cells ===")
print("By protection status:")
if 'is_protected_forest' in missing_info.columns:
    protected_count = missing_info['is_protected_forest'].sum()
    print(f"  Protected forest: {protected_count}")
    print(f"  Non-protected: {len(missing_info) - protected_count}")

print("By island status:")
if 'is_island' in missing_info.columns:
    island_count = missing_info['is_island'].sum() 
    print(f"  Island: {island_count}")
    print(f"  Mainland: {len(missing_info) - island_count}")

print("By population:")
zero_pop = (missing_info['population'] == 0).sum()
low_pop = ((missing_info['population'] > 0) & (missing_info['population'] < 1)).sum()
med_pop = (missing_info['population'] >= 1).sum()
print(f"  Zero population: {zero_pop}")
print(f"  Low population (<1): {low_pop}")  
print(f"  Medium+ population (≥1): {med_pop}")

print("By general location:")
location_counts = missing_info['location_desc'].value_counts()
print(location_counts.to_string())

print(f"\n=== Solution ===")
print("To fix the spatial gaps in rendering:")
print("1. These 25 specific grid cells need CatBoost/RF predictions generated")
print("2. Check why these cells were excluded from model training")
print("3. If they lack sufficient training data, use spatial interpolation")
print("4. Or mark them as 'no data available' rather than missing entirely")

# Save missing cell locations for further investigation
missing_cells[['grid_id', 'geometry', 'lon', 'lat', 'barangay_name_clean', 
               'population', 'is_protected_forest', 'is_island']].to_file(
    "missing_prediction_cells.geojson", driver="GeoJSON")
    
print(f"\nSaved missing cell locations to: missing_prediction_cells.geojson")