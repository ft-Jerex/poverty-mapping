#!/usr/bin/env python3
"""
Create a complete prediction file that includes both labeled and unlabeled areas.
"""

import pandas as pd
from pathlib import Path

print("=== Creating Complete Prediction Coverage ===")

# Load the separate prediction files
labeled_cat = pd.read_csv("data/catBoost/geospatial_disagg/grid_predictions.csv")
unlabeled_cat = pd.read_csv("data/catBoost/geospatial_disagg/unlabeled_grid_predictions.csv") 
labeled_rf = pd.read_csv("data/rf/geospatial_disagg/grid_predictions.csv")
unlabeled_rf = pd.read_csv("data/rf/geospatial_disagg/unlabeled_grid_predictions.csv")

print(f"Loaded data:")
print(f"  Labeled CatBoost: {len(labeled_cat)} rows")
print(f"  Unlabeled CatBoost: {len(unlabeled_cat)} rows") 
print(f"  Labeled RF: {len(labeled_rf)} rows")
print(f"  Unlabeled RF: {len(unlabeled_rf)} rows")

# Check column names
print(f"\nColumn structures:")
print(f"  Labeled CatBoost: {list(labeled_cat.columns)}")
print(f"  Unlabeled CatBoost: {list(unlabeled_cat.columns)}")

# Standardize column names and combine labeled + unlabeled for CatBoost
labeled_cat_clean = labeled_cat.copy()
labeled_cat_clean['grid_id'] = labeled_cat_clean['grid_cell_id'] 
labeled_cat_clean['pred_scaled_catboost'] = labeled_cat_clean['pred_raw']

unlabeled_cat_clean = unlabeled_cat.copy()  
unlabeled_cat_clean['grid_id'] = unlabeled_cat_clean['grid_cell_id']
unlabeled_cat_clean['pred_scaled_catboost'] = unlabeled_cat_clean['pred']

# Do the same for RF
labeled_rf_clean = labeled_rf.copy()
labeled_rf_clean['grid_id'] = labeled_rf_clean['grid_cell_id']
labeled_rf_clean['pred_scaled_rf'] = labeled_rf_clean['pred_raw'] 

unlabeled_rf_clean = unlabeled_rf.copy()
unlabeled_rf_clean['grid_id'] = unlabeled_rf_clean['grid_cell_id'] 
unlabeled_rf_clean['pred_scaled_rf'] = unlabeled_rf_clean['pred']

# Combine labeled + unlabeled for each model
catboost_complete = pd.concat([
    labeled_cat_clean[['grid_id', 'pred_scaled_catboost', 'barangay_name_clean']],
    unlabeled_cat_clean[['grid_id', 'pred_scaled_catboost', 'barangay_name_clean']]
], ignore_index=True)

rf_complete = pd.concat([
    labeled_rf_clean[['grid_id', 'pred_scaled_rf', 'barangay_name_clean']], 
    unlabeled_rf_clean[['grid_id', 'pred_scaled_rf', 'barangay_name_clean']]
], ignore_index=True)

print(f"\nAfter combining:")
print(f"  Complete CatBoost: {len(catboost_complete)} rows")
print(f"  Complete RF: {len(rf_complete)} rows")

# Merge CatBoost + RF predictions
complete_predictions = catboost_complete.merge(
    rf_complete[['grid_id', 'pred_scaled_rf']], 
    on='grid_id', 
    how='outer'
)

print(f"  Final combined: {len(complete_predictions)} rows")

# Load the current grid data to get coordinates
current_grid = pd.read_csv("data/grid_with_comprehensive_data.csv")
if 'grid_id' in current_grid.columns:
    # Merge with coordinate data
    complete_with_coords = complete_predictions.merge(
        current_grid[['grid_id', 'lon', 'lat']], 
        on='grid_id', 
        how='left'
    )
    print(f"  With coordinates: {len(complete_with_coords)} rows")
    
    # Save the complete predictions file
    output_file = "data/complete_grid_predictions.csv"
    complete_with_coords.to_csv(output_file, index=False)
    print(f"\nSaved complete predictions to: {output_file}")
    
    # Show coverage by barangay
    coverage_by_brgy = complete_with_coords.groupby('barangay_name_clean').size().sort_values(ascending=False)
    print(f"\nCoverage by barangay (top 10):")
    print(coverage_by_brgy.head(10).to_string())
    
    # Check for missing coordinates (areas not in current grid)
    missing_coords = complete_with_coords['lon'].isna().sum()
    if missing_coords > 0:
        print(f"\nWARNING: {missing_coords} cells missing coordinates")
        print("These cells have predictions but no spatial data")
        missing_cells = complete_with_coords[complete_with_coords['lon'].isna()]
        print(f"Missing cells: {missing_cells['grid_id'].tolist()}")

else:
    print("ERROR: No grid_id column in current grid data")

print(f"\n=== Summary ===")
print(f"This complete file should fix the spatial gaps!")
print(f"Total coverage: {len(complete_predictions)} cells")
print(f"Previous coverage: 1716 cells") 
print(f"Difference: {len(complete_predictions) - 1716} cells")

print(f"\nNext step: Update the app to use 'complete_grid_predictions.csv'")
print(f"instead of 'grid_predictions_comparison.csv'")