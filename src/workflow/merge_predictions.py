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
) -> pd.DataFrame:
    """
    Merge CatBoost and RF predictions into a single CSV.
    
    Args:
        catboost_predictions_csv: Path to CatBoost grid_predictions.csv
        rf_predictions_csv: Path to RF grid_predictions.csv
        grid_data_csv: Path to grid_with_comprehensive_data.csv (for barangay info)
        raw_gee_export_csv: Path to raw GEE export with .geo column
        output_csv: Path to write merged predictions
        output_geojson: Optional path to write GeoJSON version
        comprehensive_output_csv: Optional path to write grid_with_comprehensive_data.csv for webapp
        
    Returns:
        Merged DataFrame
    """
    # Load predictions from BOTH labeled and unlabeled files
    cat_labeled = pd.read_csv(catboost_predictions_csv)
    rf_labeled = pd.read_csv(rf_predictions_csv)
    
    # Load unlabeled predictions if they exist
    cat_unlabeled_path = catboost_predictions_csv.parent / "unlabeled_grid_predictions.csv"
    rf_unlabeled_path = rf_predictions_csv.parent / "unlabeled_grid_predictions.csv"
    
    # Prepare CatBoost predictions
    cat_labeled = cat_labeled.rename(columns={
        'pred_raw': 'pred_scaled_catboost',
        '__target__': 'target_poverty_rate',
    })
    
    if cat_unlabeled_path.exists():
        cat_unlabeled = pd.read_csv(cat_unlabeled_path)
        cat_unlabeled = cat_unlabeled.rename(columns={'pred': 'pred_scaled_catboost'})
        cat_unlabeled['target_poverty_rate'] = None
        cat_unlabeled['population'] = None
        cat_df = pd.concat([cat_labeled, cat_unlabeled], ignore_index=True)
        print(f"CatBoost: {len(cat_labeled)} labeled + {len(cat_unlabeled)} unlabeled = {len(cat_df)} total")
    else:
        cat_df = cat_labeled
        print(f"CatBoost: {len(cat_df)} predictions (no unlabeled file found)")
    
    # Prepare RF predictions
    rf_labeled = rf_labeled.rename(columns={
        'pred_raw': 'pred_scaled_rf',
    })
    
    if rf_unlabeled_path.exists():
        rf_unlabeled = pd.read_csv(rf_unlabeled_path)
        rf_unlabeled = rf_unlabeled.rename(columns={'pred': 'pred_scaled_rf'})
        rf_df = pd.concat([rf_labeled, rf_unlabeled], ignore_index=True)
        print(f"RF: {len(rf_labeled)} labeled + {len(rf_unlabeled)} unlabeled = {len(rf_df)} total")
    else:
        rf_df = rf_labeled
        print(f"RF: {len(rf_df)} predictions (no unlabeled file found)")
    
    # Merge on grid_cell_id
    merged = cat_df[['grid_cell_id', 'pred_scaled_catboost', 'barangay_name_clean', 'target_poverty_rate', 'population']].merge(
        rf_df[['grid_cell_id', 'pred_scaled_rf']],
        on='grid_cell_id',
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
    merged = merged.groupby('grid_cell_id').agg(agg_dict).reset_index()
    print(f"After aggregation: {len(merged)} unique grid cells")
    
    # Rename grid_cell_id to grid_id for app compatibility
    merged = merged.rename(columns={'grid_cell_id': 'grid_id'})
    
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
    
    # Merge geometry with predictions
    final = merged.merge(grid_geo, on='grid_id', how='left')
    final = final.merge(grid_location, on='grid_id', how='left', suffixes=('', '_loc'))
    
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
    
    # Save merged CSV with predictions
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(output_csv, index=False)
    print(f"Saved merged predictions to: {output_csv}")
    print(f"  Total grid cells: {len(final)}")
    print(f"  CatBoost predictions: {final['pred_scaled_catboost'].notna().sum()}")
    print(f"  RF predictions: {final['pred_scaled_rf'].notna().sum()}")
    print(f"  Has .geo column: {'.geo' in final.columns}")
    
    # Create comprehensive data CSV for webapp (with .geo)
    if comprehensive_output_csv and '.geo' in final.columns:
        # Create grid_with_comprehensive_data.csv with required columns for app.py
        comp_cols = ['grid_id', '.geo', 'barangay_name_clean']
        if 'lon' in final.columns:
            comp_cols.append('lon')
        if 'lat' in final.columns:
            comp_cols.append('lat')
        if 'x_idx' in final.columns:
            comp_cols.extend(['x_idx', 'y_idx'])
        
        comprehensive_df = final[[col for col in comp_cols if col in final.columns]].copy()
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
    
    parser = argparse.ArgumentParser(description="Merge model predictions for web app")
    parser.add_argument("--povmap-backend", type=str, 
                       default=r"C:\Users\Admin\povmapbackend",
                       help="Path to povmapbackend directory")
    parser.add_argument("--output-dir", type=str,
                       default=r"C:\Users\Admin\Downloads\poverty-mapping-withbackend\poverty-mapping-withbackend\data",
                       help="Output directory for merged predictions")
    args = parser.parse_args()
    
    backend = Path(args.povmap_backend)
    output = Path(args.output_dir)
    
    # Merge CatBoost and RF predictions
    merge_model_predictions(
        catboost_predictions_csv=backend / "output" / "catBoost" / "geospatial_disagg" / "grid_predictions.csv",
        rf_predictions_csv=backend / "output" / "rf" / "geospatial_disagg" / "grid_predictions.csv",
        grid_data_csv=backend / "assets" / "grid_with_comprehensive_data.csv",
        raw_gee_export_csv=backend / "googleEarthExports" / "zc04_grid_data_2024.csv",
        output_csv=output / "grid_predictions_comparison.csv",
        output_geojson=output / "grid_with_comprehensive_data.geojson",
        comprehensive_output_csv=output / "grid_with_comprehensive_data.csv",
    )


if __name__ == "__main__":
    main()
