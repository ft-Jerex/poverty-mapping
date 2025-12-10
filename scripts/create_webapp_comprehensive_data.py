"""
Create comprehensive data file for webapp with all features properly aligned.
"""
import pandas as pd
import geopandas as gpd
import numpy as np
from pathlib import Path

def main():
    project_root = Path(__file__).parent.parent
    
    # Load grid GPKG (defines the grid cells for webapp)
    grid_path = project_root / "data" / "grid_1km_all.gpkg"
    grid = gpd.read_file(str(grid_path))
    print(f"Grid GPKG: {len(grid)} cells")
    
    # Convert cell_id to x_y format grid_id
    def cell_id_to_grid_id(cell_id):
        parts = str(cell_id).split('_')
        if len(parts) == 3 and parts[0] == 'cell':
            return f"{int(parts[1])}_{int(parts[2])}"
        return cell_id
    
    grid['grid_id'] = grid['cell_id'].apply(cell_id_to_grid_id)
    
    # Get centroids for coordinate matching
    grid_wgs84 = grid.to_crs("EPSG:4326")
    grid_wgs84['centroid'] = grid_wgs84.geometry.centroid
    grid_wgs84['lon'] = grid_wgs84['centroid'].x
    grid_wgs84['lat'] = grid_wgs84['centroid'].y
    
    # Load assets comprehensive data (has features but different grid format)
    assets_path = project_root / "assets" / "grid_with_comprehensive_data.csv"
    assets_df = pd.read_csv(assets_path)
    print(f"Assets data: {len(assets_df)} rows")
    
    # Assets data has lon/lat - use spatial matching
    if 'lon' in assets_df.columns and 'lat' in assets_df.columns:
        print("Using spatial matching based on lon/lat...")
        
        # Get unique grid cells from assets by averaging features per cell
        # Group by approximate location (round to grid precision)
        from scipy.spatial import cKDTree
        
        # Build KD-tree from grid centroids
        grid_coords = grid_wgs84[['lon', 'lat']].values
        tree = cKDTree(grid_coords)
        
        # Find nearest grid cell for each asset row
        asset_coords = assets_df[['lon', 'lat']].values
        distances, indices = tree.query(asset_coords)
        
        # Assign grid_id based on nearest grid cell
        assets_df['matched_grid_id'] = [grid_wgs84.iloc[i]['grid_id'] for i in indices]
        assets_df['match_distance'] = distances
        
        # Filter to matches within reasonable distance (0.01 degrees ~ 1km)
        good_matches = assets_df[assets_df['match_distance'] < 0.015].copy()
        print(f"Good matches: {len(good_matches)} rows")
        
        # Aggregate features by matched grid_id
        feature_cols = [
            'elevation', 'modis_ndvi', 'ndbi', 'ndvi', 'nighttime_lights',
            'poi_accessibility', 'population', 'precipitation', 'road_accessibility',
            'slope', 'surface_temp', 'sentinel2_composite',
            'NIR_glcm_contrast', 'NIR_glcm_dissimilarity', 'NIR_glcm_homogeneity', 'NIR_glcm_energy',
            'NIR_glcm_correlation', 'NIR_glcm_asm', 'Red_glcm_contrast', 'Red_glcm_dissimilarity',
            'Red_glcm_homogeneity', 'Red_glcm_energy', 'Red_glcm_correlation', 'Red_glcm_asm',
            'NIR_std', 'Red_std', 'NIR_mean', 'Red_mean'
        ]
        
        # Check which columns exist
        available_features = [f for f in feature_cols if f in good_matches.columns]
        print(f"Available features: {len(available_features)}")
        
        # Also check for alternative column names (case variations)
        col_mapping = {}
        for col in good_matches.columns:
            lower_col = col.lower()
            if lower_col == 'ndvi' and 'ndvi' not in available_features:
                col_mapping[col] = 'ndvi'
            elif lower_col == 'population_left' and 'population' not in available_features:
                col_mapping[col] = 'population'
        
        # Rename columns
        good_matches = good_matches.rename(columns=col_mapping)
        available_features = [f for f in feature_cols if f in good_matches.columns]
        print(f"After mapping: {len(available_features)} features")
        
        # Aggregate by grid_id (mean of numeric features)
        agg_dict = {f: 'mean' for f in available_features}
        if 'barangay_name_clean' in good_matches.columns:
            agg_dict['barangay_name_clean'] = 'first'
        if 'poverty_rate' in good_matches.columns:
            agg_dict['poverty_rate'] = 'first'
        
        aggregated = good_matches.groupby('matched_grid_id').agg(agg_dict).reset_index()
        aggregated = aggregated.rename(columns={'matched_grid_id': 'grid_id'})
        print(f"Aggregated: {len(aggregated)} unique grid cells")
        
        # Merge with grid to get full coverage
        result = grid_wgs84[['grid_id', 'lon', 'lat']].merge(aggregated, on='grid_id', how='left')
        print(f"After merge: {len(result)} rows")
        
        # Fill missing values with spatial interpolation
        missing_count = result['elevation'].isna().sum() if 'elevation' in result.columns else len(result)
        print(f"Missing feature values: {missing_count} cells")
        
        if missing_count > 0 and missing_count < len(result):
            print("Filling missing values with nearest neighbor...")
            from scipy.spatial import cKDTree
            
            missing_mask = result['elevation'].isna() if 'elevation' in result.columns else pd.Series([True]*len(result))
            valid_mask = ~missing_mask
            
            if valid_mask.sum() > 0:
                valid_coords = result.loc[valid_mask, ['lon', 'lat']].values
                missing_coords = result.loc[missing_mask, ['lon', 'lat']].values
                
                tree = cKDTree(valid_coords)
                distances, indices = tree.query(missing_coords, k=3)
                
                for feat in available_features:
                    if feat not in result.columns:
                        continue
                    valid_vals = result.loc[valid_mask, feat].values
                    filled = []
                    for dists, idxs in zip(distances, indices):
                        weights = 1.0 / (dists + 1e-10)
                        weights /= weights.sum()
                        filled.append(np.average(valid_vals[idxs], weights=weights))
                    result.loc[missing_mask, feat] = filled
        
        # Save result
        output_path = project_root / "data" / "grid_with_comprehensive_data.csv"
        result.to_csv(output_path, index=False)
        print(f"Saved to {output_path}")
        print(f"Final: {len(result)} rows, {len(result.columns)} columns")
        print(f"Columns: {list(result.columns)}")
        
        # Verify
        verify = pd.read_csv(output_path)
        print(f"\nVerification:")
        print(f"  Rows: {len(verify)}")
        if 'elevation' in verify.columns:
            print(f"  elevation: {verify['elevation'].notna().sum()} valid")
        if 'nighttime_lights' in verify.columns:
            print(f"  nighttime_lights: {verify['nighttime_lights'].notna().sum()} valid")


if __name__ == "__main__":
    main()
