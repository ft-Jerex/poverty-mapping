"""
Model inference module for running predictions with trained CatBoost and RF models.

This module loads trained models from the models/ directory and provides
inference functions that can be called from the refresh pipeline.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from sklearn.preprocessing import RobustScaler

warnings.filterwarnings("ignore")


# ============================================================================
# CONFIGURATION
# ============================================================================

NB_RADII_M = [500, 1000, 2000]

GEOSPATIAL_FEATURES = [
    'elevation', 'modis_ndvi', 'ndbi', 'ndvi', 'nighttime_lights',
    'poi_accessibility', 'population', 'precipitation', 'road_accessibility', 'sentinel2_composite',
    'slope', 'surface_temp', 'x_idx_y', 'y_idx_y', 'cluster', 'is_protected_forest', 'is_island',
    'NIR_glcm_contrast', 'NIR_glcm_dissimilarity', 'NIR_glcm_homogeneity', 'NIR_glcm_energy',
    'NIR_glcm_correlation', 'NIR_glcm_asm', 'Red_glcm_contrast', 'Red_glcm_dissimilarity',
    'Red_glcm_homogeneity', 'Red_glcm_energy', 'Red_glcm_correlation', 'Red_glcm_asm',
    'NIR_std', 'Red_std', 'NIR_mean', 'Red_mean'
]


# ============================================================================
# FEATURE ENGINEERING (same as training scripts)
# ============================================================================

def spatial_knn_impute_features(df: pd.DataFrame, features: list, k_neighbors: int = 5) -> pd.DataFrame:
    """Impute missing feature values using spatial KNN."""
    if 'lon' not in df.columns or 'lat' not in df.columns:
        return df.fillna(df.median(numeric_only=True))
    
    df = df.copy()
    coords = df[['lon', 'lat']].values
    tree = cKDTree(coords)
    
    for feat in features:
        if feat not in df.columns:
            continue
        missing_mask = df[feat].isna().values
        if not missing_mask.any():
            continue
        
        distances, indices = tree.query(coords[missing_mask], k=k_neighbors + 1)
        imputed = []
        valid_mask = ~missing_mask
        
        for dists, idxs in zip(distances, indices):
            neighbor_vals = [df.iloc[idx][feat] for d, idx in zip(dists, idxs) if d > 1e-12 and valid_mask[idx]]
            neighbor_dists = [d for d, idx in zip(dists, idxs) if d > 1e-12 and valid_mask[idx]]
            if neighbor_vals:
                w = np.array(neighbor_dists)
                w = 1.0 / (w + 1e-10)
                w /= w.sum()
                imputed.append(np.average(neighbor_vals, weights=w))
            else:
                imputed.append(df[feat].median())
        df.loc[missing_mask, feat] = imputed
    
    return df.fillna(df.median(numeric_only=True))


def add_spatial_aggregates_for_radius(df: pd.DataFrame, features: list, radius_m: int) -> Tuple[pd.DataFrame, list]:
    """Add spatial aggregates for a given radius."""
    meters_per_degree = 111000.0
    radius_deg = radius_m / meters_per_degree
    coords = df[['lon', 'lat']].values
    tree = cKDTree(coords)
    new_cols = []
    df2 = df.copy()
    
    for feat in features:
        if feat not in df2.columns:
            continue
        means, meds, stds = [], [], []
        vals = df2[feat].values
        for pt in coords:
            idxs = tree.query_ball_point(pt, r=radius_deg)
            neigh = vals[idxs]
            neigh = neigh[~pd.isna(neigh)]
            if len(neigh) == 0:
                means.append(np.nan)
                meds.append(np.nan)
                stds.append(np.nan)
            else:
                means.append(np.mean(neigh))
                meds.append(np.median(neigh))
                stds.append(np.std(neigh))
        for suffix, arr in zip(['mean', 'med', 'std'], [means, meds, stds]):
            col = f"{feat}_nb_{suffix}_r{radius_m}"
            df2[col] = arr
            new_cols.append(col)
    
    for c in new_cols:
        if c in df2.columns:
            df2[c].fillna(df2[c].median(), inplace=True)
    
    return df2, new_cols


def add_multiscale_aggregates(df: pd.DataFrame, features: list, radii_m: list) -> Tuple[pd.DataFrame, list]:
    """Add multi-scale spatial aggregates."""
    df2 = df.copy()
    all_new = []
    for r in radii_m:
        df2, new_cols = add_spatial_aggregates_for_radius(df2, features, r)
        all_new.extend(new_cols)
    return df2, all_new


def add_spatial_trend_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, list]:
    """Add spatial trend features (coordinate-based)."""
    df2 = df.copy()
    if not {'lon', 'lat'}.issubset(df2.columns):
        return df2, []
    
    df2['lon'] = pd.to_numeric(df2['lon'], errors='coerce')
    df2['lat'] = pd.to_numeric(df2['lat'], errors='coerce')
    df2['lon2'] = df2['lon'] ** 2
    df2['lat2'] = df2['lat'] ** 2
    df2['lon_lat'] = df2['lon'] * df2['lat']
    df2['sin_lon'] = np.sin(df2['lon'] * 2 * np.pi / 360)
    df2['cos_lon'] = np.cos(df2['lon'] * 2 * np.pi / 360)
    df2['sin_lat'] = np.sin(df2['lat'] * 2 * np.pi / 360)
    df2['cos_lat'] = np.cos(df2['lat'] * 2 * np.pi / 360)
    
    new = ['lon', 'lat', 'lon2', 'lat2', 'lon_lat', 'sin_lon', 'cos_lon', 'sin_lat', 'cos_lat']
    return df2, new


def add_simple_interactions(X: pd.DataFrame, max_pairs: int = 8) -> Tuple[pd.DataFrame, list]:
    """Add interaction features."""
    X2 = X.copy()
    numeric = X2.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric) <= 1:
        return X2, []
    
    variances = X2[numeric].var().sort_values(ascending=False)
    chosen = variances.index.tolist()[:6]
    new_cols = []
    pairs = []
    
    for i in range(len(chosen)):
        for j in range(i + 1, len(chosen)):
            if len(pairs) >= max_pairs:
                break
            a, b = chosen[i], chosen[j]
            X2[f'{a}__x__{b}'] = X2[a] * X2[b]
            den = X2[b].replace(0, np.nan)
            X2[f'{a}__div__{b}'] = (X2[a] / den).replace([np.inf, -np.inf], np.nan)
            new_cols.extend([f'{a}__x__{b}', f'{a}__div__{b}'])
            pairs.append((a, b))
        if len(pairs) >= max_pairs:
            break
    
    return X2, new_cols


def prepare_features_for_inference(
    df: pd.DataFrame,
    feature_columns: list,
    base_features: Optional[list] = None,
) -> pd.DataFrame:
    """
    Prepare features for model inference.
    
    This applies the same feature engineering as training but aligns
    columns to match the saved feature_columns.json.
    """
    if base_features is None:
        base_features = [f for f in GEOSPATIAL_FEATURES if f in df.columns]
    
    # Feature engineering pipeline
    df_imp = spatial_knn_impute_features(df, base_features)
    df_ms, _ = add_multiscale_aggregates(df_imp, base_features, NB_RADII_M)
    df_trend, _ = add_spatial_trend_features(df_ms)
    
    # Get all engineered features
    all_feats = [f for f in base_features if f in df_trend.columns]
    for col in df_trend.columns:
        if '_nb_' in col or col in ['lon2', 'lat2', 'lon_lat', 'sin_lon', 'cos_lon', 'sin_lat', 'cos_lat']:
            all_feats.append(col)
    all_feats = list(set(all_feats))
    
    for f in all_feats:
        df_trend[f] = pd.to_numeric(df_trend[f], errors='coerce')
    
    X_raw = df_trend[all_feats].copy().fillna(df_trend[all_feats].median())
    X_inter, _ = add_simple_interactions(X_raw)
    X_final = X_inter.copy()
    
    # Clean up infinities
    X_final.replace([np.inf, -np.inf], np.nan, inplace=True)
    X_final = X_final.fillna(X_final.median(numeric_only=True))
    
    # Scale features
    numeric_cols = X_final.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        X_final[numeric_cols] = RobustScaler().fit_transform(X_final[numeric_cols])
    
    # Align to expected feature columns (from training)
    X_aligned = X_final.reindex(columns=feature_columns, fill_value=0)
    
    return X_aligned


# ============================================================================
# MODEL LOADING
# ============================================================================

def load_catboost_model(model_path: Path):
    """Load a trained CatBoost model."""
    from catboost import CatBoostRegressor
    model = CatBoostRegressor()
    model.load_model(str(model_path))
    return model


def load_rf_model(model_path: Path):
    """Load a trained Random Forest model."""
    import joblib
    return joblib.load(model_path)


def load_feature_columns(feature_columns_path: Path) -> list:
    """Load feature column order from training."""
    with open(feature_columns_path, 'r') as f:
        return json.load(f)


# ============================================================================
# INFERENCE FUNCTIONS
# ============================================================================

def run_catboost_inference(
    df: pd.DataFrame,
    model_path: Path,
    feature_columns_path: Path,
) -> pd.DataFrame:
    """
    Run CatBoost model inference on new data.
    
    Args:
        df: DataFrame with preprocessed grid data
        model_path: Path to catboost_disagg_model.cbm
        feature_columns_path: Path to feature_columns.json
        
    Returns:
        DataFrame with predictions added
    """
    model = load_catboost_model(model_path)
    feature_cols = load_feature_columns(feature_columns_path)
    
    X = prepare_features_for_inference(df, feature_cols)
    predictions = model.predict(X)
    
    df = df.copy()
    df['pred_catboost'] = predictions
    
    return df


def run_rf_inference(
    df: pd.DataFrame,
    model_path: Path,
    feature_columns_path: Path,
) -> pd.DataFrame:
    """
    Run Random Forest model inference on new data.
    
    Args:
        df: DataFrame with preprocessed grid data
        model_path: Path to rf_disagg_model.pkl
        feature_columns_path: Path to feature_columns.json
        
    Returns:
        DataFrame with predictions added
    """
    model = load_rf_model(model_path)
    feature_cols = load_feature_columns(feature_columns_path)
    
    X = prepare_features_for_inference(df, feature_cols)
    predictions = model.predict(X)
    
    df = df.copy()
    df['pred_rf'] = predictions
    
    return df


def run_all_models(
    preprocessed_csv: Path,
    models_dir: Path,
    output_dir: Path,
    povmap_backend_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """
    Run inference with all available models.
    
    Args:
        preprocessed_csv: Path to grid_with_comprehensive_data.csv
        models_dir: Path to models directory
        output_dir: Path to write prediction outputs
        povmap_backend_dir: Path to povmapbackend (for feature columns)
        
    Returns:
        Dictionary mapping model names to output file paths
    """
    df = pd.read_csv(preprocessed_csv)
    
    # Create grid_id if not present
    if 'grid_id' not in df.columns:
        if 'x_idx' in df.columns and 'y_idx' in df.columns:
            df['grid_id'] = df['x_idx'].astype(str) + '_' + df['y_idx'].astype(str)
    
    outputs = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine output_dir for feature columns if not provided
    if povmap_backend_dir is None:
        # Use workspace-relative output directory
        povmap_backend_dir = Path(__file__).parent.parent.parent
    
    # CatBoost
    catboost_model = models_dir / "catboost_disagg_model.cbm"
    catboost_features = models_dir / "catboost_feature_columns.json"
    
    if catboost_model.exists():
        print("Running CatBoost inference...")
        # Use feature columns from povmapbackend if not in models dir
        if not catboost_features.exists():
            catboost_features = povmap_backend_dir / "output" / "catBoost" / "geospatial_disagg" / "feature_columns.json"
        
        if catboost_features.exists():
            df = run_catboost_inference(df, catboost_model, catboost_features)
            outputs['catboost'] = output_dir / "catboost_predictions.csv"
        else:
            print(f"Warning: CatBoost feature columns not found at {catboost_features}")
    
    # Random Forest
    rf_model = models_dir / "rf_disagg_model.pkl"
    rf_features = models_dir / "rf_feature_columns.json"
    
    if rf_model.exists():
        print("Running RF inference...")
        if not rf_features.exists():
            rf_features = povmap_backend_dir / "output" / "rf" / "geospatial_disagg" / "feature_columns.json"
        
        if rf_features.exists():
            df = run_rf_inference(df, rf_model, rf_features)
            outputs['rf'] = output_dir / "rf_predictions.csv"
        else:
            print(f"Warning: RF feature columns not found at {rf_features}")
    
    # Aggregate to grid level and save
    grid_cols = ['grid_id']
    if 'pred_catboost' in df.columns:
        grid_cols.append('pred_catboost')
    if 'pred_rf' in df.columns:
        grid_cols.append('pred_rf')
    if 'barangay_name_clean' in df.columns:
        grid_cols.append('barangay_name_clean')
    if 'lon' in df.columns:
        grid_cols.append('lon')
    if 'lat' in df.columns:
        grid_cols.append('lat')
    
    # Aggregate samples to grid cells
    agg_dict = {}
    if 'pred_catboost' in df.columns:
        agg_dict['pred_catboost'] = 'mean'
    if 'pred_rf' in df.columns:
        agg_dict['pred_rf'] = 'mean'
    if 'barangay_name_clean' in df.columns:
        agg_dict['barangay_name_clean'] = 'first'
    if 'lon' in df.columns:
        agg_dict['lon'] = 'mean'
    if 'lat' in df.columns:
        agg_dict['lat'] = 'mean'
    
    if agg_dict:
        grid_preds = df.groupby('grid_id').agg(agg_dict).reset_index()
    else:
        grid_preds = df[['grid_id']].drop_duplicates()
    
    # Rename for app compatibility
    if 'pred_catboost' in grid_preds.columns:
        grid_preds = grid_preds.rename(columns={'pred_catboost': 'pred_scaled_catboost'})
    if 'pred_rf' in grid_preds.columns:
        grid_preds = grid_preds.rename(columns={'pred_rf': 'pred_scaled_rf'})
    
    # Save merged predictions
    merged_path = output_dir / "grid_predictions_comparison.csv"
    grid_preds.to_csv(merged_path, index=False)
    outputs['merged'] = merged_path
    
    print(f"Saved merged predictions to: {merged_path}")
    print(f"  Total grids: {len(grid_preds)}")
    
    return outputs
