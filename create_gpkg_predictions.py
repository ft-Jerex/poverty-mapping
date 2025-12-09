#!/usr/bin/env python3
"""
Create complete predictions for all cells in the GPKG grid using spatial interpolation for missing cells.
"""

import geopandas as gpd
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.neighbors import KNeighborsRegressor

print("=== Creating Complete GPKG-Based Predictions ===")

# Load the complete GPKG grid
gpkg_path = Path("data/grid_1km_all.gpkg")
grid_gdf = gpd.read_file(gpkg_path)
print(f"Complete GPKG grid: {len(grid_gdf)} cells")
print(f"GPKG columns: {list(grid_gdf.columns)}")

# Load existing predictions
complete_preds = pd.read_csv("data/complete_grid_predictions.csv")
print(f"Existing predictions: {len(complete_preds)} cells")

# Check the grid ID column name in GPKG
grid_id_col = None
for col in ['cell_id', 'grid_id', 'id']:
    if col in grid_gdf.columns:
        grid_id_col = col
        break

if grid_id_col is None:
    print("ERROR: No suitable grid ID column found in GPKG")
    print(f"Available columns: {list(grid_gdf.columns)}")
    exit(1)

print(f"Using grid ID column: {grid_id_col}")

# Merge existing predictions with the complete grid
if grid_id_col != 'grid_id':
    grid_gdf = grid_gdf.rename(columns={grid_id_col: 'grid_id'})

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
    
    # Get centroids for spatial interpolation
    if merged_gdf.crs.is_projected:
        centroids = merged_gdf.geometry.centroid
    else:
        # Convert to projected CRS for better distance calculations
        projected = merged_gdf.to_crs(merged_gdf.estimate_utm_crs())
        centroids = projected.geometry.centroid
        centroids = centroids.to_crs(merged_gdf.crs)
    
    merged_gdf['x'] = centroids.x
    merged_gdf['y'] = centroids.y
    
    # Interpolate CatBoost predictions
    if missing_catboost > 0:
        # Use cells with valid CatBoost predictions for training
        train_mask = has_catboost
        if train_mask.sum() >= 3:  # Need at least 3 points for interpolation
            X_train = merged_gdf.loc[train_mask, ['x', 'y']].values
            y_train = merged_gdf.loc[train_mask, 'pred_scaled_catboost'].values
            
            # Predict for missing cells
            predict_mask = ~has_catboost
            X_predict = merged_gdf.loc[predict_mask, ['x', 'y']].values
            
            # Use k=min(5, available_points) for k-nearest neighbors
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

# Fill missing barangay names with empty string for consistency
merged_gdf['barangay_name_clean'] = merged_gdf['barangay_name_clean'].fillna('')

# Create the final complete predictions file
final_columns = ['grid_id', 'pred_scaled_catboost', 'pred_scaled_rf', 'barangay_name_clean', 'x', 'y']
available_columns = [col for col in final_columns if col in merged_gdf.columns]

# Add lon/lat if available, otherwise use x/y
if 'lon' in merged_gdf.columns and 'lat' in merged_gdf.columns:
    available_columns.extend(['lon', 'lat'])
elif 'x' in merged_gdf.columns and 'y' in merged_gdf.columns:
    # Rename x,y to lon,lat for consistency with API expectations
    merged_gdf['lon'] = merged_gdf['x']
    merged_gdf['lat'] = merged_gdf['y']
    available_columns.extend(['lon', 'lat'])

complete_predictions_final = merged_gdf[available_columns].copy()

# Save the complete predictions
output_file = "data/gpkg_complete_predictions.csv"
complete_predictions_final.to_csv(output_file, index=False)

print(f"\n=== Results ===")
print(f"Complete predictions saved to: {output_file}")
print(f"Total cells: {len(complete_predictions_final)}")
print(f"CatBoost coverage: {complete_predictions_final['pred_scaled_catboost'].notna().sum()}")
print(f"RF coverage: {complete_predictions_final['pred_scaled_rf'].notna().sum()}")

# Also save as GeoJSON for spatial verification
geojson_output = "data/gpkg_complete_predictions.geojson"
merged_gdf[['grid_id', 'pred_scaled_catboost', 'pred_scaled_rf', 'barangay_name_clean', 'geometry']].to_file(
    geojson_output, driver='GeoJSON'
)
print(f"Spatial data saved to: {geojson_output}")

print(f"\nNext: Update app.py to use '{output_file}' for complete coverage")