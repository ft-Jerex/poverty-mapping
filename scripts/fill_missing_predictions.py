"""
Fill missing predictions using spatial interpolation.
Ensures 100% coverage of ROI grid cells.
"""
import geopandas as gpd
import pandas as pd
import numpy as np
from scipy.spatial import cKDTree
from pathlib import Path

def main():
    project_root = Path(__file__).parent.parent
    
    # Load grid GPKG
    grid_path = project_root / "data" / "grid_1km_all.gpkg"
    grid_gdf = gpd.read_file(str(grid_path))
    print(f"Grid GPKG: {len(grid_gdf)} cells")
    
    # Convert cell_id to grid_id format
    def cell_id_to_grid_id(cell_id):
        parts = str(cell_id).split('_')
        if len(parts) == 3 and parts[0] == 'cell':
            return f"{int(parts[1])}_{int(parts[2])}"
        return cell_id
    
    grid_gdf['grid_id'] = grid_gdf['cell_id'].apply(cell_id_to_grid_id)
    
    # Load predictions
    preds_path = project_root / "data" / "grid_predictions_comparison.csv"
    preds = pd.read_csv(preds_path)
    print(f"Current predictions: {len(preds)} grids")
    
    # Merge
    gdf = grid_gdf.merge(preds, on='grid_id', how='left')
    
    # Get centroids for spatial interpolation
    gdf['centroid'] = gdf.geometry.centroid
    gdf['lon_c'] = gdf['centroid'].x
    gdf['lat_c'] = gdf['centroid'].y
    
    # Find missing cells
    missing_mask = gdf['pred_scaled_catboost'].isna()
    valid_mask = ~missing_mask
    
    print(f"Valid predictions: {valid_mask.sum()}")
    print(f"Missing predictions: {missing_mask.sum()}")
    
    if missing_mask.sum() > 0:
        print("Filling missing cells using nearest neighbor interpolation...")
        
        # Build KD-tree from valid cells
        valid_coords = gdf.loc[valid_mask, ['lon_c', 'lat_c']].values
        tree = cKDTree(valid_coords)
        
        # Find nearest neighbors for missing cells
        missing_coords = gdf.loc[missing_mask, ['lon_c', 'lat_c']].values
        distances, indices = tree.query(missing_coords, k=3)
        
        # Interpolate using inverse distance weighting
        for col in ['pred_scaled_catboost', 'pred_scaled_rf']:
            if col not in gdf.columns:
                continue
            valid_vals = gdf.loc[valid_mask, col].values
            filled = []
            for dists, idxs in zip(distances, indices):
                weights = 1.0 / (dists + 1e-10)
                weights /= weights.sum()
                filled.append(np.average(valid_vals[idxs], weights=weights))
            gdf.loc[missing_mask, col] = filled
        
        print(f"Filled! New coverage: {gdf['pred_scaled_catboost'].notna().sum()} / {len(gdf)}")
    
    # Save updated predictions
    output_cols = ['grid_id', 'pred_scaled_catboost', 'pred_scaled_rf']
    
    # Add any additional columns from original predictions
    for col in preds.columns:
        if col not in output_cols and col in gdf.columns:
            output_cols.append(col)
    
    # Keep only columns that exist
    output_cols = [c for c in output_cols if c in gdf.columns]
    output_df = gdf[output_cols].copy()
    output_df.to_csv(preds_path, index=False)
    print(f"Saved updated predictions to {preds_path}")
    
    # Verify
    verify = pd.read_csv(preds_path)
    print(f"\nFinal verification:")
    print(f"  Total grids: {len(verify)}")
    print(f"  CatBoost coverage: {verify['pred_scaled_catboost'].notna().sum()}")
    print(f"  RF coverage: {verify['pred_scaled_rf'].notna().sum()}")
    print(f"  CatBoost range: {verify['pred_scaled_catboost'].min():.3f} - {verify['pred_scaled_catboost'].max():.3f}")


if __name__ == "__main__":
    main()
