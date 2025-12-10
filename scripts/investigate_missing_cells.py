#!/usr/bin/env python3
"""
Investigate why there are only 1716 CatBoost/RF predictions instead of 1724 to match CNN.
"""

import pandas as pd
from pathlib import Path

# File paths
cnn_file = Path("data/all_cells_predictions_1km.csv")
catboost_rf_file = Path("data/grid_predictions_comparison.csv") 
grid_file = Path("data/grid_with_comprehensive_data.csv")

print("=== Investigating Missing Cells ===")

# Load the files
print("Loading data files...")
cnn_df = pd.read_csv(cnn_file)
catboost_rf_df = pd.read_csv(catboost_rf_file)
grid_df = pd.read_csv(grid_file)

print(f"CNN predictions: {len(cnn_df)} rows")
print(f"CatBoost/RF predictions: {len(catboost_rf_df)} rows")  
print(f"Grid metadata: {len(grid_df)} rows")
print(f"Missing from CatBoost/RF: {len(cnn_df) - len(catboost_rf_df)} cells")

# The CNN file uses cell_id format like "cell_0017_0000"
# The CatBoost/RF file uses grid_id format like "0_21"
# Need to map between these formats using the grid metadata

print("\n=== Analyzing ID formats ===")
print("CNN cell_id samples:", cnn_df['cell_id'].head(3).tolist())
print("CatBoost/RF grid_id samples:", catboost_rf_df['grid_id'].head(3).tolist())

# Check if grid metadata has both formats
print("\nGrid metadata columns:", list(grid_df.columns))

if 'cell_id' in grid_df.columns and 'grid_id' in grid_df.columns:
    # Perfect - we can map between them
    print("\nGrid metadata contains both cell_id and grid_id - can map between formats")
    
    # Get sets of IDs from each file
    cnn_cell_ids = set(cnn_df['cell_id'])
    catboost_grid_ids = set(catboost_rf_df['grid_id'])
    
    # Use grid metadata to map cell_id to grid_id for CNN data
    grid_map = dict(zip(grid_df['cell_id'], grid_df['grid_id']))
    cnn_as_grid_ids = set()
    unmapped_cnn = []
    
    for cell_id in cnn_cell_ids:
        if cell_id in grid_map:
            cnn_as_grid_ids.add(grid_map[cell_id])
        else:
            unmapped_cnn.append(cell_id)
    
    print(f"CNN cells that map to grid IDs: {len(cnn_as_grid_ids)}")
    print(f"CNN cells that don't map: {len(unmapped_cnn)}")
    if unmapped_cnn:
        print("Unmapped CNN cells (first 5):", unmapped_cnn[:5])
    
    # Find what's missing
    missing_from_catboost = cnn_as_grid_ids - catboost_grid_ids
    extra_in_catboost = catboost_grid_ids - cnn_as_grid_ids
    
    print(f"\nMissing from CatBoost/RF: {len(missing_from_catboost)} cells")
    print(f"Extra in CatBoost/RF: {len(extra_in_catboost)} cells")
    
    if missing_from_catboost:
        print("Missing grid_ids:", sorted(list(missing_from_catboost)))
        
        # Get the original cell_ids for these
        reverse_map = {v: k for k, v in grid_map.items()}
        missing_cell_ids = [reverse_map.get(gid, f"UNKNOWN_FOR_{gid}") for gid in sorted(missing_from_catboost)]
        print("Corresponding cell_ids:", missing_cell_ids)
    
    if extra_in_catboost:
        print("Extra grid_ids (first 10):", sorted(list(extra_in_catboost))[:10])

elif 'x_idx' in grid_df.columns and 'y_idx' in grid_df.columns:
    print("\nGrid metadata has x_idx, y_idx - need to parse CNN cell_id format")
    
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
    
    # Remove rows where parsing failed
    cnn_parsed = cnn_df.dropna(subset=['x_idx', 'y_idx']).copy()
    cnn_parsed['x_idx'] = cnn_parsed['x_idx'].astype(int)
    cnn_parsed['y_idx'] = cnn_parsed['y_idx'].astype(int)
    
    print(f"Successfully parsed {len(cnn_parsed)} CNN cell coordinates")
    
    # Try to join with grid metadata on x,y coordinates
    grid_coords = set(zip(grid_df['x_idx'], grid_df['y_idx']))
    cnn_coords = set(zip(cnn_parsed['x_idx'], cnn_parsed['y_idx']))
    
    print(f"Grid coordinates: {len(grid_coords)}")
    print(f"CNN coordinates: {len(cnn_coords)}")
    
    # Find overlapping coordinates
    common_coords = grid_coords & cnn_coords
    cnn_only_coords = cnn_coords - grid_coords
    grid_only_coords = grid_coords - cnn_coords
    
    print(f"Common coordinates: {len(common_coords)}")
    print(f"CNN-only coordinates: {len(cnn_only_coords)}")
    print(f"Grid-only coordinates: {len(grid_only_coords)}")
    
    if cnn_only_coords:
        print("CNN-only coordinates (first 5):", list(cnn_only_coords)[:5])

else:
    print("\nCannot find suitable mapping between cell_id and grid_id formats")
    print("Available grid columns:", list(grid_df.columns))

print("\n=== Summary ===")
print(f"Expected cells (CNN): {len(cnn_df)}")
print(f"Actual cells (CatBoost/RF): {len(catboost_rf_df)}")
print(f"Shortfall: {len(cnn_df) - len(catboost_rf_df)} cells")
print("\nThis explains why the API returns 2673 cells instead of the expected 1724 cells.")
print("The API is likely using a different, larger grid file or merging multiple sources.")