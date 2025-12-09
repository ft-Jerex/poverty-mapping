#!/usr/bin/env python3
"""
Create complete predictions by mapping between GPKG cell_id format and prediction grid_id format.
"""

import geopandas as gpd
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.neighbors import KNeighborsRegressor

print("=== Creating Complete GPKG-Based Predictions (Fixed ID Mapping) ===")

# Load the complete GPKG grid
gpkg_path = Path("data/grid_1km_all.gpkg")
grid_gdf = gpd.read_file(gpkg_path)
print(f"Complete GPKG grid: {len(grid_gdf)} cells")

# Load existing predictions
complete_preds = pd.read_csv("data/complete_grid_predictions.csv")
print(f"Existing predictions: {len(complete_preds)} cells")

# Convert GPKG cell_id format to grid_id format
def cell_id_to_grid_id(cell_id):
    """Convert 'cell_0017_0000' to '17_0' format"""
    try:
        parts = str(cell_id).split('_')
        if len(parts) == 3 and parts[0] == 'cell':
            x = int(parts[1])  # Remove leading zeros
            y = int(parts[2])  # Remove leading zeros
            return f"{x}_{y}"
    except:
        pass
    return None

# Convert grid_id format to cell_id format  
def grid_id_to_cell_id(grid_id):
    """Convert '17_0' to 'cell_0017_0000' format"""
    try:
        parts = str(grid_id).split('_')
        if len(parts) == 2:
            x = int(parts[0])
            y = int(parts[1])
            return f"cell_{x:04d}_{y:04d}"
    except:
        pass
    return None

# Add grid_id column to GPKG data
grid_gdf['grid_id'] = grid_gdf['cell_id'].apply(cell_id_to_grid_id)
print(f"Successfully mapped {grid_gdf['grid_id'].notna().sum()} cell_ids to grid_ids")

# Show sample mappings
print("\nSample ID mappings:")
for i in range(5):
    cell_id = grid_gdf.iloc[i]['cell_id']
    grid_id = grid_gdf.iloc[i]['grid_id']
    print(f"  {cell_id} -> {grid_id}")

# Merge existing predictions with the complete grid
merged_gdf = grid_gdf.merge(complete_preds, on='grid_id', how='left')
print(f"After merge: {len(merged_gdf)} cells")

# Count cells with and without predictions
has_catboost = merged_gdf['pred_scaled_catboost'].notna()
has_rf = merged_gdf['pred_scaled_rf'].notna()
missing_catboost = (~has_catboost).sum()
missing_rf = (~has_rf).sum()

print(f"\nPrediction coverage:")
print(f"  CatBoost: {has_catboost.sum()} cells ({missing_catboost} missing)")
print(f"  RF: {has_rf.sum()} cells ({missing_rf} missing)")

# For cells missing predictions, use spatial interpolation from nearby cells
if missing_catboost > 0 or missing_rf > 0:
    print(f"\nApplying spatial interpolation for missing predictions...")
    
    # Use the centroid coordinates from GPKG
    merged_gdf['x'] = merged_gdf['centroid_lon']
    merged_gdf['y'] = merged_gdf['centroid_lat']
    
    # Interpolate CatBoost predictions
    if missing_catboost > 0:
        train_mask = has_catboost
        if train_mask.sum() >= 3:
            X_train = merged_gdf.loc[train_mask, ['x', 'y']].values
            y_train = merged_gdf.loc[train_mask, 'pred_scaled_catboost'].values
            
            predict_mask = ~has_catboost
            X_predict = merged_gdf.loc[predict_mask, ['x', 'y']].values
            
            k = min(5, len(y_train))
            knn_cat = KNeighborsRegressor(n_neighbors=k)
            knn_cat.fit(X_train, y_train)
            
            predicted_catboost = knn_cat.predict(X_predict)
            merged_gdf.loc[predict_mask, 'pred_scaled_catboost'] = predicted_catboost
            
            print(f"  Interpolated {len(predicted_catboost)} CatBoost predictions")
    
    # Interpolate RF predictions
    if missing_rf > 0:
        train_mask = has_rf
        if train_mask.sum() >= 3:
            X_train = merged_gdf.loc[train_mask, ['x', 'y']].values
            y_train = merged_gdf.loc[train_mask, 'pred_scaled_rf'].values
            
            predict_mask = ~has_rf
            X_predict = merged_gdf.loc[predict_mask, ['x', 'y']].values
            
            k = min(5, len(y_train))
            knn_rf = KNeighborsRegressor(n_neighbors=k)
            knn_rf.fit(X_train, y_train)
            
            predicted_rf = knn_rf.predict(X_predict)
            merged_gdf.loc[predict_mask, 'pred_scaled_rf'] = predicted_rf
            
            print(f"  Interpolated {len(predicted_rf)} RF predictions")

# Ensure predictions are in valid range [0, 1]
merged_gdf['pred_scaled_catboost'] = merged_gdf['pred_scaled_catboost'].clip(0, 1)
merged_gdf['pred_scaled_rf'] = merged_gdf['pred_scaled_rf'].clip(0, 1)

# Fill missing barangay names with empty string
merged_gdf['barangay_name_clean'] = merged_gdf['barangay_name_clean'].fillna('')

# Create the final complete predictions file with proper coordinates
final_data = merged_gdf[['grid_id', 'pred_scaled_catboost', 'pred_scaled_rf', 'barangay_name_clean']].copy()
final_data['lon'] = merged_gdf['centroid_lon'] 
final_data['lat'] = merged_gdf['centroid_lat']

# Save the complete predictions
output_file = "data/gpkg_complete_predictions.csv"
final_data.to_csv(output_file, index=False)

print(f"\n=== Results ===")
print(f"Complete predictions saved to: {output_file}")
print(f"Total cells: {len(final_data)}")
print(f"CatBoost coverage: {final_data['pred_scaled_catboost'].notna().sum()}")
print(f"RF coverage: {final_data['pred_scaled_rf'].notna().sum()}")

# Show sample of final data
print(f"\nSample final predictions:")
print(final_data.head().to_string(index=False))

print(f"\nNext: Update app.py to use '{output_file}' for complete coverage")