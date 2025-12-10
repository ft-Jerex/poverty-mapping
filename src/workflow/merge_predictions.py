"""
Merge CatBoost and RF model predictions into a unified format for the web app.

This script reads the grid-level predictions from both models and creates
the grid_predictions_comparison.csv file that app.py expects.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import pandas as pd
import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree


def fill_missing_predictions_spatial(df: pd.DataFrame, pred_col: str, k_neighbors: int = 5) -> pd.DataFrame:
    """
    Fill missing predictions using spatial interpolation (IDW).
    
    Args:
        df: DataFrame with lon, lat, and prediction column
        pred_col: Name of the prediction column to fill
        k_neighbors: Number of nearest neighbors to use for interpolation
        
    Returns:
        DataFrame with filled predictions
    """
    if pred_col not in df.columns:
        return df
    
    if 'lon' not in df.columns or 'lat' not in df.columns:
        print(f"Warning: Cannot fill {pred_col} - missing lon/lat columns")
        return df
    
    df = df.copy()
    missing_mask = df[pred_col].isna()
    
    if not missing_mask.any():
        print(f"{pred_col}: No missing values to fill")
        return df
    
    valid_mask = ~missing_mask
    if valid_mask.sum() == 0:
        print(f"Warning: No valid {pred_col} values to interpolate from")
        return df
    
    # Get valid coordinates and values
    valid_coords = df.loc[valid_mask, ['lon', 'lat']].values
    valid_values = df.loc[valid_mask, pred_col].values
    missing_coords = df.loc[missing_mask, ['lon', 'lat']].values
    
    # Build KD-tree for nearest neighbor search
    tree = cKDTree(valid_coords)
    k = min(k_neighbors, len(valid_coords))
    
    # Query for k nearest neighbors
    distances, indices = tree.query(missing_coords, k=k)
    
    # Inverse distance weighting
    filled_values = []
    for i in range(len(missing_coords)):
        if k == 1:
            dists = np.array([distances[i]])
            idxs = np.array([indices[i]])
        else:
            dists = distances[i]
            idxs = indices[i]
        
        # Inverse distance weights
        weights = 1.0 / (dists + 1e-6)
        weights = weights / weights.sum()
        
        # Weighted average
        interpolated = np.sum(valid_values[idxs] * weights)
        filled_values.append(interpolated)
    
    # Fill missing values
    df.loc[missing_mask, pred_col] = filled_values
    
    print(f"{pred_col}: Filled {missing_mask.sum()} missing predictions using spatial interpolation")
    return df


def merge_model_predictions(
    catboost_predictions_csv: Path,
    rf_predictions_csv: Path,
    grid_data_csv: Path,
    raw_gee_export_csv: Path,
    output_csv: Path,
    output_geojson: Optional[Path] = None,
    comprehensive_output_csv: Optional[Path] = None,
    grid_gpkg_path: Optional[Path] = None,
) -> pd.DataFrame:
    """
    Merge CatBoost and RF predictions into a single CSV.
    
    Uses grid_1km_all.gpkg as the authoritative grid source to ensure
    all ROI cells are included (matches CNN grid).
    
    Args:
        catboost_predictions_csv: Path to CatBoost grid_predictions.csv
        rf_predictions_csv: Path to RF grid_predictions.csv
        grid_data_csv: Path to grid_with_comprehensive_data.csv (for barangay info)
        raw_gee_export_csv: Path to raw GEE export with .geo column
        output_csv: Path to write merged predictions
        output_geojson: Optional path to write GeoJSON version
        comprehensive_output_csv: Optional path to write grid_with_comprehensive_data.csv for webapp
        grid_gpkg_path: Optional path to grid_1km_all.gpkg (authoritative grid)
        
    Returns:
        Merged DataFrame
    """
    # Load predictions from BOTH labeled and unlabeled files
    cat_labeled = pd.read_csv(catboost_predictions_csv)
    rf_labeled = pd.read_csv(rf_predictions_csv)
    
    # Load unlabeled predictions if they exist (legacy training outputs)
    cat_unlabeled_path = catboost_predictions_csv.parent / "unlabeled_grid_predictions.csv"
    rf_unlabeled_path = rf_predictions_csv.parent / "unlabeled_grid_predictions.csv"
    
    # Prepare CatBoost predictions
    # Legacy format: grid_cell_id + pred_raw / __target__
    if 'pred_raw' in cat_labeled.columns:
        cat_labeled = cat_labeled.rename(columns={
            'pred_raw': 'pred_scaled_catboost',
            '__target__': 'target_poverty_rate',
        })
    elif 'pred_scaled_catboost' not in cat_labeled.columns and 'pred' in cat_labeled.columns:
        # Fallback: rename generic pred column
        cat_labeled = cat_labeled.rename(columns={'pred': 'pred_scaled_catboost'})
    
    if cat_unlabeled_path.exists():
        cat_unlabeled = pd.read_csv(cat_unlabeled_path)
        if 'pred_scaled_catboost' not in cat_unlabeled.columns and 'pred' in cat_unlabeled.columns:
            cat_unlabeled = cat_unlabeled.rename(columns={'pred': 'pred_scaled_catboost'})
        cat_unlabeled['target_poverty_rate'] = None
        cat_unlabeled['population'] = None
        cat_df = pd.concat([cat_labeled, cat_unlabeled], ignore_index=True)
        print(f"CatBoost: {len(cat_labeled)} labeled + {len(cat_unlabeled)} unlabeled = {len(cat_df)} total")
    else:
        cat_df = cat_labeled
        print(f"CatBoost: {len(cat_df)} predictions (no unlabeled file found)")
    
    # Prepare RF predictions
    if 'pred_raw' in rf_labeled.columns:
        rf_labeled = rf_labeled.rename(columns={'pred_raw': 'pred_scaled_rf'})
    elif 'pred_scaled_rf' not in rf_labeled.columns and 'pred' in rf_labeled.columns:
        rf_labeled = rf_labeled.rename(columns={'pred': 'pred_scaled_rf'})
    
    if rf_unlabeled_path.exists():
        rf_unlabeled = pd.read_csv(rf_unlabeled_path)
        if 'pred_scaled_rf' not in rf_unlabeled.columns and 'pred' in rf_unlabeled.columns:
            rf_unlabeled = rf_unlabeled.rename(columns={'pred': 'pred_scaled_rf'})
        rf_df = pd.concat([rf_labeled, rf_unlabeled], ignore_index=True)
        print(f"RF: {len(rf_labeled)} labeled + {len(rf_unlabeled)} unlabeled = {len(rf_df)} total")
    else:
        rf_df = rf_labeled
        print(f"RF: {len(rf_df)} predictions (no unlabeled file found)")
    
    # Normalize IDs: convert legacy grid_cell_id (e.g., cell_0001_0021) -> grid_id (e.g., 1_21)
    def _cell_to_grid_id(val: str) -> str:
        try:
            s = str(val)
            parts = s.replace('cell_', '').split('_')
            return f"{int(parts[0])}_{int(parts[1])}"
        except Exception:
            return str(val)

    if 'grid_id' not in cat_df.columns and 'grid_cell_id' in cat_df.columns:
        cat_df['grid_id'] = cat_df['grid_cell_id'].apply(_cell_to_grid_id)
    if 'grid_id' not in rf_df.columns and 'grid_cell_id' in rf_df.columns:
        rf_df['grid_id'] = rf_df['grid_cell_id'].apply(_cell_to_grid_id)

    # Always use grid_id going forward
    id_col = 'grid_id'
    # Drop legacy column to avoid accidental selection downstream
    if 'grid_cell_id' in cat_df.columns:
        cat_df = cat_df.drop(columns=['grid_cell_id'])
    if 'grid_cell_id' in rf_df.columns:
        rf_df = rf_df.drop(columns=['grid_cell_id'])

    # Merge on chosen ID column
    cat_merge_cols = [id_col, 'pred_scaled_catboost']
    if 'barangay_name_clean' in cat_df.columns:
        cat_merge_cols.append('barangay_name_clean')
    if 'target_poverty_rate' in cat_df.columns:
        cat_merge_cols.append('target_poverty_rate')
    if 'population' in cat_df.columns:
        cat_merge_cols.append('population')

    rf_merge_cols = [id_col, 'pred_scaled_rf']

    merged = cat_df[cat_merge_cols].merge(
        rf_df[rf_merge_cols],
        on=id_col,
        how='outer'
    )

    print(f"Merged {len(merged)} predictions (before aggregation)")
    
    # Aggregate to grid level (average predictions for multiple samples per grid)
    agg_dict = {
        'pred_scaled_catboost': 'mean',
        'pred_scaled_rf': 'mean',
        'barangay_name_clean': 'first',
        'target_poverty_rate': 'first',
        'population': 'first'
    }
    merged = merged.groupby(id_col).agg(agg_dict).reset_index()
    print(f"After aggregation: {len(merged)} unique grid cells")
    
    # Rename ID column to grid_id for app compatibility
    if id_col != 'grid_id':
        merged = merged.rename(columns={id_col: 'grid_id'})
    
    # Load RAW GEE export to get .geo column (JSON format geometry)
    print(f"Loading raw GEE export from: {raw_gee_export_csv}")
    raw_gee_df = pd.read_csv(raw_gee_export_csv)
    
    # Create grid_id from x_idx and y_idx if not present
    if 'grid_id' not in raw_gee_df.columns:
        if 'x_idx' in raw_gee_df.columns and 'y_idx' in raw_gee_df.columns:
            raw_gee_df['grid_id'] = raw_gee_df['x_idx'].astype(str) + '_' + raw_gee_df['y_idx'].astype(str)
    
    # Get unique grid cells with .geo (JSON geometry)
    geo_cols = ['grid_id']
    if '.geo' in raw_gee_df.columns:
        geo_cols.append('.geo')
    if 'lon' in raw_gee_df.columns:
        geo_cols.append('lon')
    if 'lat' in raw_gee_df.columns:
        geo_cols.append('lat')
    if 'x_idx' in raw_gee_df.columns:
        geo_cols.append('x_idx')
    if 'y_idx' in raw_gee_df.columns:
        geo_cols.append('y_idx')
    
    # Aggregate to grid level (take first occurrence for geometry)
    grid_geo = raw_gee_df[geo_cols].drop_duplicates(subset=['grid_id'])
    
    # Load preprocessed data for barangay info
    grid_df = pd.read_csv(grid_data_csv)
    if 'grid_id' not in grid_df.columns:
        if 'x_idx' in grid_df.columns and 'y_idx' in grid_df.columns:
            grid_df['grid_id'] = grid_df['x_idx'].astype(str) + '_' + grid_df['y_idx'].astype(str)
    
    # Get barangay and location info (including lon/lat)
    location_cols = ['grid_id', 'barangay_name_clean']
    if 'lon' in grid_df.columns:
        location_cols.append('lon')
    if 'lat' in grid_df.columns:
        location_cols.append('lat')
    
    grid_location = grid_df[location_cols].drop_duplicates(subset=['grid_id'])

    # Use CNN grid (grid_1km_all.gpkg) as authoritative source for full ROI coverage
    # This ensures all grid cells are included, not just those with GEE data
    all_grids = None
    if grid_gpkg_path is None:
        grid_gpkg_path = output_csv.parent / "grid_1km_all.gpkg"
    
    if grid_gpkg_path and Path(grid_gpkg_path).exists():
        print(f"Loading authoritative grid from: {grid_gpkg_path}")
        cnn_grid = gpd.read_file(grid_gpkg_path)
        print(f"Grid columns: {cnn_grid.columns.tolist()}")
        
        # Convert cell_id format (cell_0000_0021 -> 0_21) to match other data
        def convert_cell_id(cell_id):
            try:
                parts = str(cell_id).replace('cell_', '').split('_')
                return str(int(parts[0])) + '_' + str(int(parts[1]))
            except Exception:
                return str(cell_id)
        
        # Handle different column naming conventions
        if 'cell_id' in cnn_grid.columns:
            cnn_grid['grid_id'] = cnn_grid['cell_id'].apply(convert_cell_id)
        elif 'grid_id' in cnn_grid.columns:
            pass  # Already has grid_id
        elif 'id' in cnn_grid.columns:
            cnn_grid['grid_id'] = cnn_grid['id'].apply(convert_cell_id)
        else:
            # Generate grid_id from index
            cnn_grid['grid_id'] = cnn_grid.index.astype(str)
            print("Warning: No cell_id column found, using index as grid_id")
        
        # Handle different coordinate column names
        if 'centroid_lon' in cnn_grid.columns:
            cnn_grid['lon'] = cnn_grid['centroid_lon']
            cnn_grid['lat'] = cnn_grid['centroid_lat']
        elif 'lon' not in cnn_grid.columns:
            # Calculate centroid from geometry
            centroids = cnn_grid.geometry.centroid.to_crs(epsg=4326)
            cnn_grid['lon'] = centroids.x
            cnn_grid['lat'] = centroids.y
        
        # Get geometry as GeoJSON for .geo column
        try:
            cnn_grid_wgs84 = cnn_grid.to_crs(epsg=4326) if cnn_grid.crs and cnn_grid.crs != 'EPSG:4326' else cnn_grid
            cnn_grid['.geo'] = cnn_grid_wgs84.geometry.apply(
                lambda g: json.dumps({'type': 'Polygon', 'coordinates': [list(g.exterior.coords)]}) if g and hasattr(g, 'exterior') else None
            )
        except Exception as e:
            print(f"Warning: Could not convert geometry to GeoJSON: {e}")
            cnn_grid['.geo'] = None
        
        # Build output columns
        output_cols = ['grid_id', 'lon', 'lat', '.geo']
        if 'cell_id' in cnn_grid.columns:
            output_cols.insert(1, 'cell_id')
        
        all_grids = cnn_grid[[c for c in output_cols if c in cnn_grid.columns]].copy()
        print(f"Authoritative grid has {len(all_grids)} cells")
    else:
        print(f"Warning: grid_gpkg not found at {grid_gpkg_path}, using GEE-based grid")
        all_grids = grid_location[['grid_id']].drop_duplicates()
    
    # Start from full grid and left-join predictions
    final = all_grids.merge(merged, on='grid_id', how='left')
    
    # Merge geometry from GEE if not already present
    if '.geo' not in final.columns or final['.geo'].isna().all():
        final = final.merge(grid_geo[['grid_id', '.geo']], on='grid_id', how='left', suffixes=('', '_gee'))
        if '.geo_gee' in final.columns:
            final['.geo'] = final['.geo'].fillna(final['.geo_gee'])
            final = final.drop(columns=['.geo_gee'])
    
    # Merge barangay info
    final = final.merge(grid_location[['grid_id', 'barangay_name_clean']], on='grid_id', how='left', suffixes=('', '_loc'))
    
    # Handle barangay name column duplicates
    if 'barangay_name_clean_loc' in final.columns:
        final['barangay_name_clean'] = final['barangay_name_clean'].fillna(final['barangay_name_clean_loc'])
        final = final.drop(columns=['barangay_name_clean_loc'])
    
    # Fill missing predictions using spatial interpolation
    print("\nFilling missing predictions with spatial interpolation...")
    if 'lon' in final.columns and 'lat' in final.columns:
        final = fill_missing_predictions_spatial(final, 'pred_scaled_catboost', k_neighbors=5)
        final = fill_missing_predictions_spatial(final, 'pred_scaled_rf', k_neighbors=5)
    else:
        print("Warning: Cannot fill missing predictions - lon/lat columns not found")
    
    # Optional: Backfill missing RF/CatBoost predictions using CNN (complete ROI)
    try:
        cnn_csv = output_csv.parent / "all_cells_predictions_1km.csv"
        if cnn_csv.exists():
            cnn_df = pd.read_csv(cnn_csv)
            
            # CNN uses cell_id format (cell_0000_0021), convert to grid_id (0_21)
            if 'cell_id' in cnn_df.columns and 'grid_id' not in cnn_df.columns:
                def convert_cell_id(cell_id):
                    parts = cell_id.replace('cell_', '').split('_')
                    return str(int(parts[0])) + '_' + str(int(parts[1]))
                cnn_df['grid_id'] = cnn_df['cell_id'].apply(convert_cell_id)
            
            # Rename prediction column
            if 'predicted_poverty' in cnn_df.columns:
                cnn_df = cnn_df.rename(columns={'predicted_poverty': 'cnn_pred'})

            if 'grid_id' in cnn_df.columns and 'cnn_pred' in cnn_df.columns:
                print(f"CNN data: {len(cnn_df)} cells with predictions")
                final = final.merge(cnn_df[['grid_id', 'cnn_pred']], on='grid_id', how='left')
                # Backfill: only fill missing values
                if 'pred_scaled_catboost' in final.columns:
                    missing_cat = final['pred_scaled_catboost'].isna().sum()
                    final['pred_scaled_catboost'] = final['pred_scaled_catboost'].fillna(final['cnn_pred'])
                    filled_cat = missing_cat - final['pred_scaled_catboost'].isna().sum()
                    if filled_cat > 0:
                        print(f"CNN backfill: filled {filled_cat} missing CatBoost predictions")
                if 'pred_scaled_rf' in final.columns:
                    missing_rf = final['pred_scaled_rf'].isna().sum()
                    final['pred_scaled_rf'] = final['pred_scaled_rf'].fillna(final['cnn_pred'])
                    filled_rf = missing_rf - final['pred_scaled_rf'].isna().sum()
                    if filled_rf > 0:
                        print(f"CNN backfill: filled {filled_rf} missing RF predictions")
            else:
                print("CNN backfill skipped: grid_id/cnn_pred columns not found")
        else:
            print("CNN backfill skipped: all_cells_predictions_1km.csv not found")
    except Exception as e:
        print(f"CNN backfill error: {str(e)}")

    # Save merged CSV with predictions
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(output_csv, index=False)
    print(f"Saved merged predictions to: {output_csv}")
    print(f"  Total grid cells: {len(final)}")
    print(f"  CatBoost predictions: {final['pred_scaled_catboost'].notna().sum()}")
    print(f"  RF predictions: {final['pred_scaled_rf'].notna().sum()}")
    print(f"  Has .geo column: {'.geo' in final.columns}")
    
    # Create comprehensive data CSV for webapp (with .geo)
    # NOTE: We merge with existing comprehensive data to preserve geospatial features
    if comprehensive_output_csv and '.geo' in final.columns:
        # Required columns for app.py
        comp_cols = ['grid_id', '.geo', 'barangay_name_clean']
        if 'lon' in final.columns:
            comp_cols.append('lon')
        if 'lat' in final.columns:
            comp_cols.append('lat')
        if 'x_idx' in final.columns:
            comp_cols.extend(['x_idx', 'y_idx'])
        
        comprehensive_df = final[[col for col in comp_cols if col in final.columns]].copy()
        
        # If existing comprehensive data has more features, merge them in
        if comprehensive_output_csv.exists():
            existing = pd.read_csv(comprehensive_output_csv)
            feature_cols = [c for c in existing.columns if c not in comprehensive_df.columns]
            if feature_cols:
                print(f"Preserving {len(feature_cols)} feature columns from existing comprehensive data")
                # Merge features from existing file
                existing_features = existing[['grid_id'] + feature_cols]
                comprehensive_df = comprehensive_df.merge(existing_features, on='grid_id', how='left')
        
        comprehensive_df.to_csv(comprehensive_output_csv, index=False)
        print(f"Saved comprehensive data to: {comprehensive_output_csv}")
        print(f"  Columns: {comprehensive_df.columns.tolist()}")
    
    # Create GeoJSON if requested
    if output_geojson and '.geo' in final.columns:
        create_geojson_from_predictions(final, output_geojson)
    
    return final


def create_geojson_from_predictions(df: pd.DataFrame, output_path: Path) -> None:
    """
    Create a GeoJSON file from predictions DataFrame with .geo column.
    """
    from shapely import wkt
    from shapely.geometry import shape
    
    features = []
    for _, row in df.iterrows():
        try:
            geo_str = row.get('.geo', None)
            if pd.isna(geo_str):
                continue
                
            # Parse geometry (could be GeoJSON or WKT)
            if isinstance(geo_str, str):
                geo_str = geo_str.strip()
                if geo_str.startswith('{'):
                    geom = shape(json.loads(geo_str))
                else:
                    geom = wkt.loads(geo_str)
            else:
                continue
            
            # Build properties
            props = {
                'grid_id': row.get('grid_id'),
                'pred_scaled_catboost': float(row['pred_scaled_catboost']) if pd.notna(row.get('pred_scaled_catboost')) else None,
                'pred_scaled_rf': float(row['pred_scaled_rf']) if pd.notna(row.get('pred_scaled_rf')) else None,
                'barangay_name_clean': row.get('barangay_name_clean'),
            }
            
            if pd.notna(row.get('lon')):
                props['lon'] = float(row['lon'])
            if pd.notna(row.get('lat')):
                props['lat'] = float(row['lat'])
            
            features.append({
                'type': 'Feature',
                'geometry': geom.__geo_interface__,
                'properties': props
            })
        except Exception as e:
            print(f"Warning: Could not parse geometry for grid {row.get('grid_id')}: {e}")
            continue
    
    geojson = {
        'type': 'FeatureCollection',
        'features': features
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(geojson, f)
    
    print(f"Saved GeoJSON to: {output_path}")
    print(f"  Features: {len(features)}")


def create_cnn_predictions_csv(
    cnn_predictions_csv: Path,
    grid_gpkg: Path,
    output_csv: Path,
) -> pd.DataFrame:
    """
    Convert CNN predictions to the format expected by app.py.
    
    The app expects:
    - cell_id: format "cell_0000_0021"
    - predicted_poverty: float value
    
    Args:
        cnn_predictions_csv: Path to CNN grid predictions (from cnn_data_preprocessing.py)
        grid_gpkg: Path to grid geopackage (grid_1km_all.gpkg) - optional, not used
        output_csv: Path to write formatted predictions
    """
    cnn_df = pd.read_csv(cnn_predictions_csv)
    
    # Rename cell_id column if using different format
    if 'cell_id' not in cnn_df.columns:
        if 'grid_id' in cnn_df.columns:
            # Convert format from "0_22" to "cell_0000_0022"
            def convert_grid_id(gid):
                parts = str(gid).split('_')
                if len(parts) == 2:
                    try:
                        return f"cell_{int(parts[0]):04d}_{int(parts[1]):04d}"
                    except ValueError:
                        return gid
                return gid
            cnn_df['cell_id'] = cnn_df['grid_id'].apply(convert_grid_id)
        elif 'grid_cell_id' in cnn_df.columns:
            def convert_grid_id(gid):
                parts = str(gid).split('_')
                if len(parts) == 2:
                    try:
                        return f"cell_{int(parts[0]):04d}_{int(parts[1]):04d}"
                    except ValueError:
                        return gid
                return gid
            cnn_df['cell_id'] = cnn_df['grid_cell_id'].apply(convert_grid_id)
    
    # Ensure predicted_poverty column exists
    if 'predicted_poverty' not in cnn_df.columns:
        if 'pred_raw' in cnn_df.columns:
            cnn_df['predicted_poverty'] = cnn_df['pred_raw']
        elif 'pred' in cnn_df.columns:
            cnn_df['predicted_poverty'] = cnn_df['pred']
        elif 'prediction' in cnn_df.columns:
            cnn_df['predicted_poverty'] = cnn_df['prediction']
    
    # Select required columns
    output_df = cnn_df[['cell_id', 'predicted_poverty']].copy()
    
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(output_csv, index=False)
    print(f"Saved CNN predictions to: {output_csv}")
    print(f"  Total cells: {len(output_df)}")
    
    return output_df


def main():
    """Run prediction merge with default paths."""
    import argparse
    
    # Use project-relative paths by default
    _PROJECT_ROOT = Path(__file__).parent.parent.parent
    
    parser = argparse.ArgumentParser(description="Merge model predictions for web app")
    parser.add_argument("--project-root", type=str, 
                       default=str(_PROJECT_ROOT),
                       help="Path to project root (poverty-mapping-withbackend)")
    parser.add_argument("--output-dir", type=str,
                       default=str(_PROJECT_ROOT / "data"),
                       help="Output directory for merged predictions")
    args = parser.parse_args()
    
    project = Path(args.project_root)
    output = Path(args.output_dir)
    
    # Merge CatBoost and RF predictions
    merge_model_predictions(
        catboost_predictions_csv=project / "output" / "catBoost" / "geospatial_disagg" / "grid_predictions.csv",
        rf_predictions_csv=project / "output" / "rf" / "geospatial_disagg" / "grid_predictions.csv",
        grid_data_csv=project / "assets" / "grid_with_comprehensive_data.csv",
        raw_gee_export_csv=project / "googleEarthExports" / "zc04_grid_data_2024.csv",
        output_csv=output / "grid_predictions_comparison.csv",
        output_geojson=output / "grid_with_comprehensive_data.geojson",
        comprehensive_output_csv=output / "grid_with_comprehensive_data.csv",
    )


if __name__ == "__main__":
    main()
