#!/usr/bin/env python3
"""
Find the specific 8 cells missing from CatBoost/RF predictions.
"""

import pandas as pd
from pathlib import Path

# File paths
cnn_file = Path("data/all_cells_predictions_1km.csv")
catboost_rf_file = Path("data/grid_predictions_comparison.csv") 
grid_file = Path("data/grid_with_comprehensive_data.csv")

print("=== Finding Missing Cells ===")

# Load the files
cnn_df = pd.read_csv(cnn_file)
catboost_rf_df = pd.read_csv(catboost_rf_file)
grid_df = pd.read_csv(grid_file)

# Parse CNN cell_id format "cell_0017_0000" to extract x,y indices
def parse_cell_id(cell_id):
    try:
        parts = str(cell_id).split('_')
        if len(parts) == 3 and parts[0] == 'cell':
            x = int(parts[1])
            y = int(parts[2])
            return x, y
    except:
        pass
    return None, None

cnn_df['x_idx'] = cnn_df['cell_id'].apply(lambda x: parse_cell_id(x)[0])
cnn_df['y_idx'] = cnn_df['cell_id'].apply(lambda x: parse_cell_id(x)[1])
cnn_parsed = cnn_df.dropna(subset=['x_idx', 'y_idx']).copy()
cnn_parsed['x_idx'] = cnn_parsed['x_idx'].astype(int)
cnn_parsed['y_idx'] = cnn_parsed['y_idx'].astype(int)

print(f"CNN predictions: {len(cnn_parsed)} cells")
print(f"CatBoost/RF predictions: {len(catboost_rf_df)} cells")

# Create a mapping from grid metadata to join CNN and CatBoost/RF data
# Grid metadata should contain both the grid_id (used by CatBoost/RF) and x_idx, y_idx (to match CNN)

# First, let's see which CNN cells can be matched to grid metadata
grid_coords = set(zip(grid_df['x_idx'], grid_df['y_idx']))
cnn_coords = set(zip(cnn_parsed['x_idx'], cnn_parsed['y_idx']))

# Get CNN cells that have corresponding grid metadata
cnn_with_grid = cnn_parsed[cnn_parsed.apply(lambda row: (row['x_idx'], row['y_idx']) in grid_coords, axis=1)].copy()
print(f"CNN cells that have grid metadata: {len(cnn_with_grid)}")

# Join CNN with grid metadata to get grid_ids
cnn_with_gridids = cnn_with_grid.merge(
    grid_df[['x_idx', 'y_idx', 'grid_id']], 
    on=['x_idx', 'y_idx'], 
    how='left'
)

print(f"CNN cells with grid_ids: {len(cnn_with_gridids)}")

# Now compare with CatBoost/RF predictions
cnn_grid_ids = set(cnn_with_gridids['grid_id'])
catboost_grid_ids = set(catboost_rf_df['grid_id'])

print(f"Unique grid_ids in CNN (that have metadata): {len(cnn_grid_ids)}")
print(f"Unique grid_ids in CatBoost/RF: {len(catboost_grid_ids)}")

# Find missing and extra
missing_from_catboost = cnn_grid_ids - catboost_grid_ids
extra_in_catboost = catboost_grid_ids - cnn_grid_ids

print(f"\nMissing from CatBoost/RF: {len(missing_from_catboost)} cells")
print(f"Extra in CatBoost/RF: {len(extra_in_catboost)} cells")

if missing_from_catboost:
    print("\nMissing grid_ids:", sorted(list(missing_from_catboost)))
    
    # Get details for missing cells
    missing_details = cnn_with_gridids[cnn_with_gridids['grid_id'].isin(missing_from_catboost)][
        ['cell_id', 'grid_id', 'x_idx', 'y_idx', 'predicted_poverty']
    ]
    print("\nDetails of missing cells:")
    print(missing_details.to_string(index=False))

if extra_in_catboost:
    print(f"\nExtra in CatBoost/RF (first 10): {sorted(list(extra_in_catboost))[:10]}")

# Check if there are any rows in CatBoost/RF with missing predictions
catboost_na_count = catboost_rf_df[['pred_scaled_catboost', 'pred_scaled_rf']].isnull().any(axis=1).sum()
print(f"\nCatBoost/RF rows with missing predictions: {catboost_na_count}")

# Check for duplicates
catboost_duplicates = catboost_rf_df['grid_id'].duplicated().sum()
cnn_duplicates = cnn_df['cell_id'].duplicated().sum()
print(f"CatBoost/RF duplicate grid_ids: {catboost_duplicates}")
print(f"CNN duplicate cell_ids: {cnn_duplicates}")

print("\n=== Recommendation ===")
print("The 8 missing cells should be added to the CatBoost/RF predictions file.")
print("These cells exist in the CNN predictions and have grid metadata, so they")
print("should have been included in the CatBoost/RF model training/prediction.")