import warnings
warnings.filterwarnings("ignore")

import os
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from sklearn.cluster import KMeans
from sklearn.preprocessing import RobustScaler
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_absolute_error, r2_score
from catboost import CatBoostRegressor
import json

# -------------------------
# CONFIG
# -------------------------
BASE_DIR = Path(__file__).resolve().parent  # scripts/
PROJECT_ROOT = BASE_DIR.parent  # project root
ASSETS_DIR = PROJECT_ROOT / 'assets'
DATA_DIR = PROJECT_ROOT / 'data'
CSV_PATH = ASSETS_DIR / 'grid_with_comprehensive_data.csv'
OUTPUT_DIR = DATA_DIR / 'catBoost' / 'geospatial_disagg'
NB_RADII_M = [500, 1000, 2000]
USE_LOGIT_TARGET = False
USE_POPULATION_FOR_SCALING = True
EM_ITERATIONS = 0
N_FOLDS = 5
RANDOM_SEED = 42

CAT_PARAMS = {
    'iterations': 300,
    'learning_rate': 0.02,
    'depth': 3,
    'l2_leaf_reg': 30,
    'random_seed': RANDOM_SEED,
    'verbose': False
}

IGNORED_COLS = set([
    'system:index','grid_id','.geo','x_idx','y_idx','lon','lat','poverty_rate','barangay_name_clean'
])

# -------------------------
# Geospatial-only features (whitelist)
# -------------------------
GEOSPATIAL_ONLY = [
    'elevation', 'modis_ndvi', 'ndbi', 'ndvi', 'nighttime_lights',
    'poi_accessibility', 'population', 'precipitation', 'road_accessibility', 'sentinel2_composite',
    'slope', 'surface_temp', 'x_idx_y', 'y_idx_y', 'cluster', 'is_protected_forest', 'is_island',
    'NIR_glcm_contrast', 'NIR_glcm_dissimilarity', 'NIR_glcm_homogeneity', 'NIR_glcm_energy',
    'NIR_glcm_correlation', 'NIR_glcm_asm', 'Red_glcm_contrast', 'Red_glcm_dissimilarity',
    'Red_glcm_homogeneity', 'Red_glcm_energy', 'Red_glcm_correlation', 'Red_glcm_asm',
    'NIR_std', 'Red_std', 'NIR_mean', 'Red_mean'
]

# -------------------------
# UTILITIES
# -------------------------
def safe_mkdir(p:Path):
    p.mkdir(parents=True, exist_ok=True)

def ensure_rate_in_01(y):
    y = np.array(y, dtype=float)
    if np.nanmax(y) > 1.5:
        y = y / 100.0
    return np.clip(y, 0.0, 1.0)

def make_positive_weights(raw_preds, method='softplus'):
    if method == 'exp':
        w = np.exp(raw_preds - np.nanmedian(raw_preds))
    else:
        w = np.log1p(np.exp(raw_preds - np.nanmedian(raw_preds)))
    return w + 1e-12

# -------------------------
# SPATIAL IMPUTATION
# -------------------------
def spatial_knn_impute_features(df, features, k_neighbors=5):
    if 'lon' not in df.columns or 'lat' not in df.columns:
        print("No coords found - using median fill.")
        return df.fillna(df.median(numeric_only=True))
    df = df.copy()
    coords = df[['lon','lat']].values
    tree = cKDTree(coords)
    for feat in features:
        if feat not in df.columns:
            continue
        missing_mask = df[feat].isna().values
        if not missing_mask.any():
            continue
        distances, indices = tree.query(coords[missing_mask], k=k_neighbors+1)
        imputed = []
        valid_mask = ~missing_mask
        for dists, idxs in zip(distances, indices):
            neighbor_vals = [df.iloc[idx][feat] for d, idx in zip(dists, idxs) if d>1e-12 and valid_mask[idx]]
            neighbor_dists = [d for d, idx in zip(dists, idxs) if d>1e-12 and valid_mask[idx]]
            if neighbor_vals:
                w = np.array(neighbor_dists)
                w = 1.0 / (w + 1e-10)
                w /= w.sum()
                imputed.append(np.average(neighbor_vals, weights=w))
            else:
                imputed.append(df[feat].median())
        df.loc[missing_mask, feat] = imputed
    return df.fillna(df.median(numeric_only=True))

# -------------------------
# MULTI-SCALE AGGREGATES
# -------------------------
def add_spatial_aggregates_for_radius(df, features, radius_m):
    meters_per_degree = 111000.0
    radius_deg = radius_m / meters_per_degree
    coords = df[['lon','lat']].values
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
            if len(neigh)==0:
                means.append(np.nan); meds.append(np.nan); stds.append(np.nan)
            else:
                means.append(np.mean(neigh)); meds.append(np.median(neigh)); stds.append(np.std(neigh))
        for suffix, arr in zip(['mean','med','std'], [means, meds, stds]):
            col = f"{feat}_nb_{suffix}_r{radius_m}"
            df2[col] = arr
            new_cols.append(col)
    for c in new_cols:
        if c in df2.columns:
            df2[c].fillna(df2[c].median(), inplace=True)
    return df2, new_cols

def add_multiscale_aggregates(df, features, radii_m):
    df2 = df.copy()
    all_new = []
    for r in radii_m:
        df2, new_cols = add_spatial_aggregates_for_radius(df2, features, r)
        all_new.extend(new_cols)
    return df2, all_new

def add_spatial_trend_features(df):
    df2 = df.copy()
    if not {'lon','lat'}.issubset(df2.columns):
        return df2, []
    df2['lon'] = pd.to_numeric(df2['lon'], errors='coerce')
    df2['lat'] = pd.to_numeric(df2['lat'], errors='coerce')
    df2['lon2'] = df2['lon']**2
    df2['lat2'] = df2['lat']**2
    df2['lon_lat'] = df2['lon']*df2['lat']
    df2['sin_lon'] = np.sin(df2['lon']*2*np.pi/360)
    df2['cos_lon'] = np.cos(df2['lon']*2*np.pi/360)
    df2['sin_lat'] = np.sin(df2['lat']*2*np.pi/360)
    df2['cos_lat'] = np.cos(df2['lat']*2*np.pi/360)
    new = ['lon','lat','lon2','lat2','lon_lat','sin_lon','cos_lon','sin_lat','cos_lat']
    return df2, new

# -------------------------
# INTERACTIONS & SCALING
# -------------------------
def add_simple_interactions(X, max_pairs=8):
    X2 = X.copy()
    numeric = X2.select_dtypes(include=[np.number]).columns.tolist()
    if len(numeric)<=1: return X2, []
    variances = X2[numeric].var().sort_values(ascending=False)
    chosen = variances.index.tolist()[:6]
    new_cols = []
    pairs = []
    for i in range(len(chosen)):
        for j in range(i+1,len(chosen)):
            if len(pairs)>=max_pairs: break
            a,b = chosen[i], chosen[j]
            X2[f'{a}__x__{b}'] = X2[a]*X2[b]
            den = X2[b].replace(0, np.nan)
            X2[f'{a}__div__{b}'] = (X2[a] / den).replace([np.inf, -np.inf], np.nan)
            new_cols.extend([f'{a}__x__{b}', f'{a}__div__{b}'])
            pairs.append((a,b))
        if len(pairs)>=max_pairs: break
    return X2, new_cols

# -------------------------
# PREPARE DATA
# -------------------------
def prepare_engineered_X(df, base_features, radii_m):
    df_imp = spatial_knn_impute_features(df, base_features)
    df_ms, ms_cols = add_multiscale_aggregates(df_imp, base_features, radii_m)
    df_trend, trend_cols = add_spatial_trend_features(df_ms)
    fe = [f for f in base_features if f in df_trend.columns] + ms_cols + trend_cols
    for f in fe:
        df_trend[f] = pd.to_numeric(df_trend[f], errors='coerce')
    X_raw = df_trend[fe].copy().fillna(df_trend[fe].median())
    X_inter, inter_cols = add_simple_interactions(X_raw)
    X_final = X_inter.copy()
    # Replace infinities produced by interactions/divisions and fill with medians before scaling
    X_final.replace([np.inf, -np.inf], np.nan, inplace=True)
    X_final = X_final.fillna(X_final.median(numeric_only=True))
    numeric_cols = X_final.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        X_final[numeric_cols] = RobustScaler().fit_transform(X_final[numeric_cols])
    return X_final, df_trend, fe+inter_cols

# -------------------------
# COMPUTE GRID CELL IDENTIFIERS AND WEIGHTS
# -------------------------
def create_grid_cell_id(df):
    """Create unique grid cell identifier from x_idx and y_idx"""
    if 'grid_id' in df.columns:
        return df['grid_id']
    elif 'x_idx' in df.columns and 'y_idx' in df.columns:
        return df['x_idx'].astype(str) + '_' + df['y_idx'].astype(str)
    else:
        # Fallback: create sequential IDs
        print("WARNING: No grid_id or x_idx/y_idx found. Creating sequential grid IDs.")
        return pd.Series(range(len(df)), index=df.index).astype(str)

def compute_sample_weights_per_grid(df, barangay_col='barangay_name_clean', grid_cell_col='grid_cell_id'):
    """
    Compute sample weights for multi-sample data:
    - For each grid cell, count how many samples it has
    - Weight = 1 / samples_per_grid (so each grid cell contributes equally)
    - For single-grid barangays, apply areal weighting on top
    """
    df = df.copy()
    
    # Count samples per grid cell
    samples_per_grid = df[grid_cell_col].value_counts()
    df['samples_in_grid'] = df[grid_cell_col].map(samples_per_grid)
    
    # Base weight: inverse of samples per grid (equal weight per grid cell)
    df['base_weight'] = 1.0 / df['samples_in_grid']
    
    # Count grids per barangay
    grids_per_barangay = df.groupby(barangay_col)[grid_cell_col].nunique()
    df['grids_in_barangay'] = df[barangay_col].map(grids_per_barangay)
    
    # For single-grid barangays, apply areal weighting
    df['sample_weight'] = df['base_weight']
    
    single_grid_mask = df['grids_in_barangay'] == 1
    if single_grid_mask.sum() > 0 and 'shape_sqkm' in df.columns:
        # Areal adjustment for small barangays
        max_area = df['shape_sqkm'].max()
        areal_factor = (df['shape_sqkm'] / max_area).clip(0.1, 1.0)
        df.loc[single_grid_mask, 'sample_weight'] *= areal_factor[single_grid_mask]
        print(f"Applied areal weighting to {single_grid_mask.sum()} samples in single-grid barangays")
    
    return df

# -------------------------
# SCALE TO BARANGAY (AGGREGATE PREDICTIONS)
# -------------------------
def scale_predictions_to_barangay(df_samples, pred_col='pred_raw', barangay_col='barangay_name_clean', 
                                 grid_cell_col='grid_cell_id', target_col='poverty_rate', 
                                 population_col='population', use_population=False):
    """
    Aggregate sample-level predictions to barangay level and scale to match target
    """
    df = df_samples.copy()
    df['__target__'] = ensure_rate_in_01(df[target_col].values)
    
    # First, aggregate samples to grid cell level (mean)
    agg_dict = {
        pred_col: 'mean',
        barangay_col: 'first',
        '__target__': 'first',
    }
    if population_col in df.columns:
        agg_dict[population_col] = 'mean'
    grid_agg = df.groupby(grid_cell_col).agg(agg_dict).reset_index()
    
    # Now scale grid predictions to match barangay targets
    scaled_preds = {}
    
    for b, sub in grid_agg.groupby(barangay_col):
        grid_ids = sub[grid_cell_col].values
        preds = sub[pred_col].values
        
        if use_population and population_col in sub.columns:
            pop = sub[population_col].values
            # Sanitize weights
            pop = np.where(np.isfinite(pop), pop, 0.0)
            wsum = pop.sum()
            if wsum > 1e-12:
                weighted_pred = float(np.dot(preds, pop) / wsum)
            else:
                weighted_pred = float(preds.mean())
            target = float(sub['__target__'].iloc[0])
            # Scale factor to match target
            scale = target / (weighted_pred + 1e-12)
            scaled = preds * scale
        
        else:
            pred_mean = preds.mean()
            target = sub['__target__'].iloc[0]
            scale = target / (pred_mean + 1e-12)
            scaled = preds * scale
        
        for grid_id, scaled_val in zip(grid_ids, scaled):
            scaled_preds[grid_id] = np.clip(scaled_val, 0, 1)
    
    # Map back to sample level
    df['pred_scaled'] = df[grid_cell_col].map(scaled_preds)
    
    return df['pred_scaled'].values, grid_agg

# -------------------------
# MAIN PIPELINE
# -------------------------
def disaggregate_pipeline(csv_path=CSV_PATH, output_dir=OUTPUT_DIR):
    outp = Path(output_dir)
    safe_mkdir(outp)
    df_all = pd.read_csv(csv_path)
    
    # ----------------- CREATE GRID CELL IDENTIFIERS -----------------
    print(f"\n=== DATA STRUCTURE ===")
    print(f"Total samples in dataset: {len(df_all)}")
    
    df_all['grid_cell_id'] = create_grid_cell_id(df_all)
    unique_grids = df_all['grid_cell_id'].nunique()
    avg_samples_per_grid = len(df_all) / unique_grids
    
    print(f"Unique grid cells: {unique_grids}")
    print(f"Average samples per grid: {avg_samples_per_grid:.1f}")
    
    # ----------------- IDENTIFY SAMPLES WITH TARGET DATA -----------------
    missing_barangay = df_all['barangay_name_clean'].isna() if 'barangay_name_clean' in df_all.columns else pd.Series([True]*len(df_all))
    missing_poverty = df_all['poverty_rate'].isna() if 'poverty_rate' in df_all.columns else pd.Series([True]*len(df_all))
    
    df_all['has_target_data'] = ~(missing_barangay | missing_poverty)
    df_all['exclusion_reason'] = ''
    df_all.loc[missing_barangay, 'exclusion_reason'] = 'no_barangay'
    df_all.loc[missing_poverty, 'exclusion_reason'] = 'no_poverty_rate'
    df_all.loc[missing_barangay & missing_poverty, 'exclusion_reason'] = 'no_barangay_or_poverty'
    
    excluded = df_all[~df_all['has_target_data']].copy()
    df = df_all[df_all['has_target_data']].copy()
    
    print(f"\nSamples WITH target data (for training): {len(df)}")
    print(f"Samples WITHOUT target data (excluded): {len(excluded)}")
    
    if len(excluded) > 0:
        print("\nExclusion breakdown:")
        print(excluded['exclusion_reason'].value_counts().to_string())
        excluded.to_csv(outp/'excluded_samples.csv', index=False)
        print(f"Saved excluded samples to: {outp/'excluded_samples.csv'}")
    
    if 'barangay_name_clean' not in df.columns or 'poverty_rate' not in df.columns:
        raise ValueError("Missing required columns: 'barangay_name_clean' or 'poverty_rate'")

    # ----------------- Select geospatial-only features -----------------
    features = [f for f in GEOSPATIAL_ONLY if f in df.columns]
    print(f"\nUsing {len(features)} geospatial features")

    # ----------------- Prepare engineered X -----------------
    print("\nEngineering features...")
    X_final, df_with_ms, all_features = prepare_engineered_X(df, features, NB_RADII_M)
    X_final = X_final.reset_index(drop=True)
    df_with_ms = df_with_ms.reset_index(drop=True)

    # Transfer necessary columns
    df_with_ms['grid_cell_id'] = df['grid_cell_id'].values
    df_with_ms['barangay_name_clean'] = df['barangay_name_clean'].values
    df_with_ms['poverty_rate'] = df['poverty_rate'].values
    # Canonicalize population column for scaling: prefer barangay pop, fallback to sample pop
    pop_col = None
    for c in ['population', 'population_right', 'population_left']:
        if c in df.columns:
            pop_col = c
            break
    if pop_col is not None:
        df_with_ms['population'] = pd.to_numeric(df[pop_col], errors='coerce')

    if 'shape_sqkm' in df.columns:
        df_with_ms['shape_sqkm'] = df['shape_sqkm'].values

    # ----------------- COMPUTE SAMPLE WEIGHTS -----------------
    df_with_ms = compute_sample_weights_per_grid(
        df_with_ms, 
        barangay_col='barangay_name_clean',
        grid_cell_col='grid_cell_id'
    )
    
    print(f"\nSample weight summary:")
    print(f"  Min: {df_with_ms['sample_weight'].min():.4f}")
    print(f"  Mean: {df_with_ms['sample_weight'].mean():.4f}")
    print(f"  Max: {df_with_ms['sample_weight'].max():.4f}")

    y_raw = ensure_rate_in_01(df_with_ms['poverty_rate'].values)
    y_train = y_raw.copy() if not USE_LOGIT_TARGET else np.clip(y_raw,1e-6,1-1e-6)
    groups = df_with_ms['barangay_name_clean'].values
    sample_weights = df_with_ms['sample_weight'].values

    # ----------------- Group CV with Sample Weights -----------------
    print("\nRunning GroupKFold CV (barangay groups) with sample weighting...")
    
    def run_group_cv(df, X, y, groups, sample_weights, cat_params, n_folds=5):
        gkf = GroupKFold(n_splits=n_folds)
        cv_pred = np.zeros(len(X))
        folds = []
        
        for fold,(tr,te) in enumerate(gkf.split(X,y,groups),1):
            Xtr,Xte = X.iloc[tr], X.iloc[te]
            ytr,yte = y[tr], y[te]
            wtr = sample_weights[tr]
            
            model = CatBoostRegressor(**cat_params)
            model.fit(Xtr, ytr, sample_weight=wtr, verbose=False)
            ypred = model.predict(Xte)
            cv_pred[te] = ypred

            # Sample-level metrics
            sample_mae = mean_absolute_error(yte, ypred)
            sample_r2 = r2_score(yte, ypred)
            
            # Grid-level metrics (aggregate samples to grid cells)
            te_df = df.iloc[te].copy()
            te_df['pred'] = ypred
            grid_agg = te_df.groupby('grid_cell_id').agg({
                'poverty_rate': 'first',
                'pred': 'mean'
            })
            grid_mae = mean_absolute_error(grid_agg['poverty_rate'], grid_agg['pred'])
            grid_r2 = r2_score(grid_agg['poverty_rate'], grid_agg['pred'])

            folds.append({
                'fold': fold,
                'sample_mae': float(sample_mae),
                'sample_r2': float(sample_r2),
                'grid_mae': float(grid_mae),
                'grid_r2': float(grid_r2)
            })

            print(f"Fold {fold}: sample_mae={sample_mae:.4f}, sample_r2={sample_r2:.4f} | grid_mae={grid_mae:.4f}, grid_r2={grid_r2:.4f}")

        return cv_pred, pd.DataFrame(folds)

    cv_pred, cv_folds = run_group_cv(df_with_ms, X_final, y_train, groups, sample_weights, CAT_PARAMS, N_FOLDS)
    cv_folds.to_csv(outp/'cv_group_folds.csv',index=False)

    # ----------------- Final model with Sample Weights -----------------
    print("\nTraining final CatBoost model on all data...")
    final_model = CatBoostRegressor(**CAT_PARAMS)
    final_model.fit(X_final, y_train, sample_weight=sample_weights, verbose=False)
    model_path = outp / 'catboost_disagg_model.cbm'
    final_model.save_model(str(model_path))
    print("Saved model to:", model_path)

    # Predictions
    preds_raw = final_model.predict(X_final)
    df_with_ms['pred_raw'] = preds_raw

    # Scale predictions to match barangay targets
    scaled_preds, grid_agg = scale_predictions_to_barangay(
        df_with_ms,
        pred_col='pred_raw',
        barangay_col='barangay_name_clean',
        grid_cell_col='grid_cell_id',
        target_col='poverty_rate',
        population_col='population',
        use_population=USE_POPULATION_FOR_SCALING and ('population' in df_with_ms.columns)
    )
    df_with_ms['pred_scaled'] = scaled_preds
    
    # Persist feature column order for inference on unlabeled grids
    feature_cols = X_final.columns.tolist()
    with open(outp / 'feature_columns.json', 'w') as f:
        json.dump(feature_cols, f)

    # Predict on grids WITHOUT target data
    if len(excluded) > 0:
        print("\nPredicting on grids WITHOUT target data...")
        features_unl = [f for f in GEOSPATIAL_ONLY if f in excluded.columns]
        X_unl, df_unl_ms, _ = prepare_engineered_X(excluded, features_unl, NB_RADII_M)
        # Align columns to training feature set and order
        X_unl = X_unl.reindex(columns=feature_cols, fill_value=0)

        # Ensure grid IDs and useful identifiers are present
        df_unl_ms['grid_cell_id'] = create_grid_cell_id(excluded)
        for col in ['lon', 'lat', 'barangay_name_clean']:
            if col in excluded.columns:
                df_unl_ms[col] = excluded[col].values
        preds_unl = final_model.predict(X_unl)
        df_unl_ms['pred_raw'] = preds_unl
        # Save unlabeled sample-level predictions (no scaling without barangay targets)
        unl_sample_cols = [c for c in ['grid_cell_id','lon','lat','barangay_name_clean','pred_raw'] if c in df_unl_ms.columns]
        df_unl_ms[unl_sample_cols].to_csv(outp/'unlabeled_sample_predictions.csv', index=False)
        # Grid-level aggregation for unlabeled
        agg_dict_unl = {'pred_raw': 'mean'}
        if 'barangay_name_clean' in df_unl_ms.columns:
            agg_dict_unl['barangay_name_clean'] = 'first'
        unl_grid = df_unl_ms.groupby('grid_cell_id').agg(agg_dict_unl).reset_index().rename(columns={'pred_raw':'pred'})
        unl_grid.to_csv(outp/'unlabeled_grid_predictions.csv', index=False)

    # Compute final metrics
    # Sample level
    sample_r2_cv = r2_score(y_train, cv_pred)
    
    # Grid level (using CV predictions)
    cv_df = df_with_ms.copy()
    cv_df['cv_pred'] = cv_pred
    cv_grid_agg = cv_df.groupby('grid_cell_id').agg({
        'poverty_rate': 'first',
        'cv_pred': 'mean'
    })
    grid_r2_cv = r2_score(cv_grid_agg['poverty_rate'], cv_grid_agg['cv_pred'])
    
    print(f"\n=== FINAL CV METRICS ===")
    print(f"Sample-level R² (OOF): {sample_r2_cv:.4f}")
    print(f"Grid-level R² (OOF): {grid_r2_cv:.4f}")

    # Save results
    df_with_ms.to_csv(outp/'sample_predictions.csv',index=False)
    print(f"\nSaved sample-level predictions to: {outp/'sample_predictions.csv'}")
    
    grid_agg.to_csv(outp/'grid_predictions.csv',index=False)
    print(f"Saved grid-level predictions to: {outp/'grid_predictions.csv'}")
    
    # Save fold metrics
    print(f"\nCV Fold Metrics:")
    print(cv_folds.to_string(index=False))


if __name__ == "__main__":
    disaggregate_pipeline(CSV_PATH, OUTPUT_DIR)