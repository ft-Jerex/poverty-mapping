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

# Base geospatial features - must match training script exactly (26 features)
# NOTE: ndvi and population are NOT included - they were not used in training
GEOSPATIAL_FEATURES = [
    'elevation', 'modis_ndvi', 'ndbi', 'nighttime_lights',
    'poi_accessibility', 'precipitation', 'road_accessibility',
    'slope', 'surface_temp', 'sentinel2_composite',
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


def add_multiscale_aggregates(df: pd.DataFrame, features: list, radii_m: list) -> Tuple[pd.DataFrame, list]:
    """
    Add neighborhood statistics at multiple scales.
    Must match training script exactly: only mean and std, not median.
    """
    meters_per_degree = 111000.0
    coords = df[['lon', 'lat']].values
    tree = cKDTree(coords)
    new_cols = []
    df2 = df.copy()
    
    for radius_m in radii_m:
        radius_deg = radius_m / meters_per_degree
        for feat in features:
            if feat not in df2.columns:
                continue
            vals = df2[feat].values
            means, stds = [], []
            for pt in coords:
                idxs = tree.query_ball_point(pt, r=radius_deg)
                neigh = vals[idxs]
                neigh = neigh[~pd.isna(neigh)]
                if len(neigh) == 0:
                    means.append(np.nan)
                    stds.append(np.nan)
                else:
                    means.append(np.mean(neigh))
                    stds.append(np.std(neigh))
            
            col_mean = f"{feat}_nb_mean_r{radius_m}"
            col_std = f"{feat}_nb_std_r{radius_m}"
            df2[col_mean] = means
            df2[col_std] = stds
            new_cols.extend([col_mean, col_std])
    
    for c in new_cols:
        if c in df2.columns:
            df2[c].fillna(df2[c].median(), inplace=True)
    
    return df2, new_cols


def add_within_barangay_features(
    df: pd.DataFrame, 
    features: list, 
    barangay_col: str = 'barangay_name_clean'
) -> Tuple[pd.DataFrame, list]:
    """
    Add features that capture deviation from barangay mean.
    These are KEY for learning sub-barangay heterogeneity.
    Must match training script exactly.
    """
    df = df.copy()
    new_cols = []
    
    if barangay_col not in df.columns:
        # No barangay info - return zeros for deviation features
        for feat in features:
            if feat not in df.columns:
                continue
            col_zscore = f"{feat}_brgy_zscore"
            col_dev = f"{feat}_brgy_dev"
            df[col_zscore] = 0.0
            df[col_dev] = 0.0
            new_cols.extend([col_zscore, col_dev])
        return df, new_cols
    
    for feat in features:
        if feat not in df.columns:
            continue
        
        # Compute barangay mean
        brgy_mean = df.groupby(barangay_col)[feat].transform('mean')
        brgy_std = df.groupby(barangay_col)[feat].transform('std').fillna(1.0)
        
        # Z-score within barangay
        col_zscore = f"{feat}_brgy_zscore"
        df[col_zscore] = (df[feat] - brgy_mean) / (brgy_std + 1e-8)
        new_cols.append(col_zscore)
        
        # Deviation from barangay mean
        col_dev = f"{feat}_brgy_dev"
        df[col_dev] = df[feat] - brgy_mean
        new_cols.append(col_dev)
    
    return df, new_cols


def prepare_features_for_inference(
    df: pd.DataFrame,
    feature_columns: list,
    base_features: Optional[list] = None,
    barangay_col: str = 'barangay_name_clean',
) -> pd.DataFrame:
    """
    Prepare features for model inference.
    
    This applies the EXACT same feature engineering as the training script
    (train_catboost_disagg_v2.py / train_rf_disagg_v2.py).
    """
    if base_features is None:
        base_features = [f for f in GEOSPATIAL_FEATURES if f in df.columns]
    
    # Step 1: Impute missing values using spatial KNN
    df_imp = spatial_knn_impute_features(df, base_features)
    
    # Step 2: Add multi-scale neighborhood aggregates (mean + std at 500m, 1000m, 2000m)
    df_ms, ms_cols = add_multiscale_aggregates(df_imp, base_features, NB_RADII_M)
    
    # Step 3: Add within-barangay deviation features (zscore + dev)
    df_dev, dev_cols = add_within_barangay_features(df_ms, base_features, barangay_col)
    
    # Combine all features: base + multiscale + within-barangay
    all_features = [f for f in base_features if f in df_dev.columns] + ms_cols + dev_cols
    
    # Convert to numeric and fill NaN
    for f in all_features:
        if f in df_dev.columns:
            df_dev[f] = pd.to_numeric(df_dev[f], errors='coerce')
    
    X = df_dev[[f for f in all_features if f in df_dev.columns]].copy()
    X = X.fillna(X.median(numeric_only=True))
    
    # Replace infinities
    X.replace([np.inf, -np.inf], np.nan, inplace=True)
    X = X.fillna(X.median(numeric_only=True))
    
    # Scale features using RobustScaler (same as training)
    scaler = RobustScaler()
    X_scaled = pd.DataFrame(
        scaler.fit_transform(X),
        columns=X.columns,
        index=X.index
    )
    
    # Align to expected feature columns (from training) - fill missing with 0
    X_aligned = X_scaled.reindex(columns=feature_columns, fill_value=0)
    
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
            catboost_features = povmap_backend_dir / "output" / "catBoost" / "constrained_disagg" / "feature_columns.json"

        if catboost_features.exists():
            df = run_catboost_inference(df, catboost_model, catboost_features)
        else:
            print(f"Warning: CatBoost feature columns not found at {catboost_features}")

    # Random Forest
    rf_model = models_dir / "rf_disagg_model.pkl"
    rf_features = models_dir / "rf_feature_columns.json"

    if rf_model.exists():
        print("Running RF inference...")
        if not rf_features.exists():
            rf_features = povmap_backend_dir / "output" / "rf" / "constrained_disagg" / "feature_columns.json"

        if rf_features.exists():
            df = run_rf_inference(df, rf_model, rf_features)
        else:
            print(f"Warning: RF feature columns not found at {rf_features}")

    # Aggregate to grid level
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

    agg_dict: Dict[str, Any] = {}
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

    models_root = output_dir

    # Save per-model grid-level predictions in standard locations
    if 'pred_scaled_catboost' in grid_preds.columns:
        cat_dir = models_root / "catBoost" / "geospatial_disagg"
        cat_dir.mkdir(parents=True, exist_ok=True)
        cat_path = cat_dir / "grid_predictions.csv"
        cat_cols = ['grid_id', 'pred_scaled_catboost']
        extra_cols = []
        for col in ['barangay_name_clean', 'lon', 'lat']:
            if col in grid_preds.columns:
                extra_cols.append(col)
        cat_df = grid_preds[cat_cols + extra_cols].copy()
        cat_df.to_csv(cat_path, index=False)
        outputs['catboost'] = cat_path

    if 'pred_scaled_rf' in grid_preds.columns:
        rf_dir = models_root / "rf" / "geospatial_disagg"
        rf_dir.mkdir(parents=True, exist_ok=True)
        rf_path = rf_dir / "grid_predictions.csv"
        rf_cols = ['grid_id', 'pred_scaled_rf']
        extra_cols = []
        for col in ['barangay_name_clean', 'lon', 'lat']:
            if col in grid_preds.columns:
                extra_cols.append(col)
        rf_df = grid_preds[rf_cols + extra_cols].copy()
        rf_df.to_csv(rf_path, index=False)
        outputs['rf'] = rf_path

    # Also save a merged comparison CSV for convenience
    merged_path = models_root / "grid_predictions_comparison.csv"
    grid_preds.to_csv(merged_path, index=False)
    outputs['merged'] = merged_path

    print(f"Saved merged predictions to: {merged_path}")
    print(f"  Total grids: {len(grid_preds)}")

    return outputs
