import warnings
warnings.filterwarnings("ignore")

import os
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from sklearn.preprocessing import RobustScaler
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.ensemble import RandomForestRegressor
from scipy.stats import spearmanr, pearsonr
import joblib
import json

# -------------------------
# CONFIG
# -------------------------
BASE_DIR = Path(__file__).resolve().parent  # scripts/
PROJECT_ROOT = BASE_DIR.parent  # project root
ASSETS_DIR = PROJECT_ROOT / 'assets'
DATA_DIR = PROJECT_ROOT / 'data'
CSV_PATH = ASSETS_DIR / 'grid_with_comprehensive_data.csv'
OUTPUT_DIR = DATA_DIR / 'rf' / 'geospatial_disagg'
DHS_MATCHED_CSV = DATA_DIR / 'dhs_grid_matched.csv'
NB_RADII_M = [500, 1000, 2000]
USE_LOGIT_TARGET = False
USE_POPULATION_FOR_SCALING = True
ENABLE_COORD_TREND_FEATURES = False
N_FOLDS = 5
RANDOM_SEED = 42

RF_PARAMS = {
    'n_estimators': 500,
    'max_depth': None,
    'max_features': 'sqrt',
    'bootstrap': True,
    'random_state': RANDOM_SEED,
    'n_jobs': -1,
    'min_samples_leaf': 5,
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
    'slope', 'surface_temp', 'is_protected_forest', 'is_island',
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
    if ENABLE_COORD_TREND_FEATURES:
        df_trend, trend_cols = add_spatial_trend_features(df_ms)
    else:
        df_trend, trend_cols = df_ms.copy(), []
    fe = [f for f in base_features if f in df_trend.columns] + ms_cols + trend_cols
    for f in fe:
        df_trend[f] = pd.to_numeric(df_trend[f], errors='coerce')
    X_raw = df_trend[fe].copy().fillna(df_trend[fe].median())
    X_inter, inter_cols = add_simple_interactions(X_raw)
    X_final = X_inter.copy()
    # Replace infinities and fill with medians before scaling
    X_final.replace([np.inf, -np.inf], np.nan, inplace=True)
    X_final = X_final.fillna(X_final.median(numeric_only=True))
    numeric_cols = X_final.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) > 0:
        X_final[numeric_cols] = RobustScaler().fit_transform(X_final[numeric_cols])
    return X_final, df_trend, fe+inter_cols

# -------------------------
# GRID IDENTIFIERS & SAMPLE WEIGHTS
# -------------------------
def create_grid_cell_id(df):
    if 'grid_id' in df.columns:
        return df['grid_id']
    elif 'x_idx' in df.columns and 'y_idx' in df.columns:
        return df['x_idx'].astype(str) + '_' + df['y_idx'].astype(str)
    else:
        print("WARNING: No grid_id or x_idx/y_idx found. Creating sequential grid IDs.")
        return pd.Series(range(len(df)), index=df.index).astype(str)

def compute_sample_weights_per_grid(df, barangay_col='barangay_name_clean', grid_cell_col='grid_cell_id'):
    df = df.copy()
    samples_per_grid = df[grid_cell_col].value_counts()
    df['samples_in_grid'] = df[grid_cell_col].map(samples_per_grid)
    df['base_weight'] = 1.0 / df['samples_in_grid']
    grids_per_barangay = df.groupby(barangay_col)[grid_cell_col].nunique()
    df['grids_in_barangay'] = df[barangay_col].map(grids_per_barangay)
    df['sample_weight'] = df['base_weight']
    single_grid_mask = df['grids_in_barangay'] == 1
    if single_grid_mask.sum() > 0 and 'shape_sqkm' in df.columns:
        max_area = df['shape_sqkm'].max()
        areal_factor = (df['shape_sqkm'] / max_area).clip(0.1, 1.0)
        df.loc[single_grid_mask, 'sample_weight'] *= areal_factor[single_grid_mask]
        print(f"Applied areal weighting to {single_grid_mask.sum()} samples in single-grid barangays")
    return df

# -------------------------
# SCALE TO BARANGAY
# -------------------------
def scale_predictions_to_barangay(df_samples, pred_col='pred_raw', barangay_col='barangay_name_clean', 
                                 grid_cell_col='grid_cell_id', target_col='poverty_rate', 
                                 population_col='population', use_population=False):
    df = df_samples.copy()
    df['__target__'] = ensure_rate_in_01(df[target_col].values)
    agg_dict = {
        pred_col: 'mean',
        barangay_col: 'first',
        '__target__': 'first',
    }
    if population_col in df.columns:
        agg_dict[population_col] = 'mean'
    grid_agg = df.groupby(grid_cell_col).agg(agg_dict).reset_index()

    scaled_preds = {}
    for b, sub in grid_agg.groupby(barangay_col):
        grid_ids = sub[grid_cell_col].values
        preds = sub[pred_col].values
        if use_population and population_col in sub.columns:
            pop = sub[population_col].values
            pop = np.where(np.isfinite(pop), pop, 0.0)
            wsum = pop.sum()
            weighted_pred = float(np.dot(preds, pop) / (wsum + 1e-12)) if wsum > 1e-12 else float(preds.mean())
            target = float(sub['__target__'].iloc[0])
            scale = target / (weighted_pred + 1e-12)
            scaled = preds * scale
        else:
            pred_mean = preds.mean()
            target = sub['__target__'].iloc[0]
            scale = target / (pred_mean + 1e-12)
            scaled = preds * scale
        for grid_id, scaled_val in zip(grid_ids, scaled):
            scaled_preds[grid_id] = np.clip(scaled_val, 0, 1)

    df['pred_scaled'] = df[grid_cell_col].map(scaled_preds)
    return df['pred_scaled'].values, grid_agg

# -------------------------
# DHS EXTERNAL VALIDATION
# -------------------------
def run_dhs_external_validation(model, X_all, df_all, feature_cols, output_dir,
                                dhs_csv=DHS_MATCHED_CSV):
    """
    Validate model predictions against DHS 2022 cluster-level wealth scores.

    For each matched DHS cluster, look up the nearest grid cell's feature vector,
    predict poverty, and correlate with the DHS mean wealth factor score (hv271).
    Higher wealth score → lower poverty, so we expect negative Spearman/Pearson ρ.
    """
    outp = Path(output_dir)
    if not dhs_csv.exists():
        print(f"\n[DHS] Matched DHS file not found at {dhs_csv}. "
              "Run scripts/prepare_dhs_validation.py first. Skipping.")
        return None

    dhs = pd.read_csv(dhs_csv)
    if len(dhs) == 0:
        print("[DHS] No matched DHS clusters. Skipping external validation.")
        return None

    print(f"\n=== DHS EXTERNAL VALIDATION ===")
    print(f"Matched DHS clusters: {len(dhs)}")

    # Build a grid_id → row-index lookup from the training dataframe
    grid_id_to_idx = {}
    for i, gid in enumerate(df_all['grid_cell_id'].values):
        if gid not in grid_id_to_idx:
            grid_id_to_idx[gid] = i

    # For each DHS cluster, find the feature row of its nearest grid cell
    matched_rows = []
    for _, row in dhs.iterrows():
        gid = row['nearest_grid_id']
        if gid in grid_id_to_idx:
            matched_rows.append({
                'cluster_id': int(row['cluster_id']),
                'dhs_wealth_score': row['mean_wealth_score'],
                'match_distance_m': row['match_distance_m'],
                'match_confidence': row['match_confidence'],
                'urban_rural': row['urban_rural'],
                'adm1_name': row['adm1_name'],
                'feature_idx': grid_id_to_idx[gid],
            })

    if len(matched_rows) == 0:
        print("[DHS] No DHS clusters could be mapped to training grid cells. Skipping.")
        return None

    matched_df = pd.DataFrame(matched_rows)
    feature_indices = matched_df['feature_idx'].values

    # Predict at those grid cells
    X_dhs = X_all.iloc[feature_indices].reset_index(drop=True)
    preds = model.predict(X_dhs)
    matched_df['predicted_poverty'] = preds

    # Compute correlations (expect negative: high wealth ↔ low poverty)
    spearman_rho, spearman_p = spearmanr(matched_df['predicted_poverty'],
                                          matched_df['dhs_wealth_score'])
    pearson_r, pearson_p = pearsonr(matched_df['predicted_poverty'],
                                     matched_df['dhs_wealth_score'])

    print(f"  Clusters used: {len(matched_df)}")
    print(f"  Spearman ρ:  {spearman_rho:+.4f}  (p={spearman_p:.4f})")
    print(f"  Pearson  r:  {pearson_r:+.4f}  (p={pearson_p:.4f})")

    # Also compute for high-confidence matches only
    hc = matched_df[matched_df['match_confidence'] == 'high']
    if len(hc) >= 5:
        sp_hc, sp_hc_p = spearmanr(hc['predicted_poverty'], hc['dhs_wealth_score'])
        pr_hc, pr_hc_p = pearsonr(hc['predicted_poverty'], hc['dhs_wealth_score'])
        print(f"  [High-conf only, n={len(hc)}]  Spearman ρ: {sp_hc:+.4f} (p={sp_hc_p:.4f}), "
              f"Pearson r: {pr_hc:+.4f} (p={pr_hc_p:.4f})")

    # Save detailed results
    matched_df.to_csv(outp / 'dhs_external_validation.csv', index=False)
    print(f"  Saved to: {outp / 'dhs_external_validation.csv'}")

    summary = {
        'n_clusters': len(matched_df),
        'spearman_rho': float(spearman_rho),
        'spearman_p': float(spearman_p),
        'pearson_r': float(pearson_r),
        'pearson_p': float(pearson_p),
    }
    return summary

# -------------------------
# MAIN PIPELINE
# -------------------------
def disaggregate_pipeline(csv_path=CSV_PATH, output_dir=OUTPUT_DIR):
    outp = Path(output_dir)
    safe_mkdir(outp)
    df_all = pd.read_csv(csv_path)

    # Structure
    print(f"\n=== DATA STRUCTURE ===")
    print(f"Total samples in dataset: {len(df_all)}")

    df_all['grid_cell_id'] = create_grid_cell_id(df_all)
    unique_grids = df_all['grid_cell_id'].nunique()
    avg_samples_per_grid = len(df_all) / unique_grids
    print(f"Unique grid cells: {unique_grids}")
    print(f"Average samples per grid: {avg_samples_per_grid:.1f}")

    # Target availability
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

    # Features
    features = [f for f in GEOSPATIAL_ONLY if f in df.columns]
    print(f"\nUsing {len(features)} geospatial features")

    # Feature engineering
    print("\nEngineering features...")
    X_final, df_with_ms, all_features = prepare_engineered_X(df, features, NB_RADII_M)
    X_final = X_final.reset_index(drop=True)
    df_with_ms = df_with_ms.reset_index(drop=True)

    # Transfer identifiers/targets
    df_with_ms['grid_cell_id'] = df['grid_cell_id'].values
    df_with_ms['barangay_name_clean'] = df['barangay_name_clean'].values
    df_with_ms['poverty_rate'] = df['poverty_rate'].values
    # Canonicalize population column for scaling
    pop_col = None
    for c in ['population', 'population_right', 'population_left']:
        if c in df.columns:
            pop_col = c
            break
    if pop_col is not None:
        df_with_ms['population'] = pd.to_numeric(df[pop_col], errors='coerce')
    if 'shape_sqkm' in df.columns:
        df_with_ms['shape_sqkm'] = df['shape_sqkm'].values

    # Sample weights
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
    y_train = y_raw.copy() if not USE_LOGIT_TARGET else np.clip(y_raw, 1e-6, 1-1e-6)
    groups = df_with_ms['barangay_name_clean'].values
    sample_weights = df_with_ms['sample_weight'].values

    # GroupKFold CV
    print("\nRunning GroupKFold CV (barangay groups) with sample weighting...")
    def run_group_cv(df_use, X, y, groups_arr, wts, rf_params, n_folds=5):
        gkf = GroupKFold(n_splits=n_folds)
        cv_pred = np.zeros(len(X))
        folds = []
        for fold,(tr,te) in enumerate(gkf.split(X, y, groups_arr), 1):
            Xtr, Xte = X.iloc[tr], X.iloc[te]
            ytr, yte = y[tr], y[te]
            wtr = wts[tr]
            model = RandomForestRegressor(**rf_params)
            model.fit(Xtr, ytr, sample_weight=wtr)
            ypred = model.predict(Xte)
            cv_pred[te] = ypred

            # Sample-level metrics
            sample_mae = mean_absolute_error(yte, ypred)
            sample_r2 = r2_score(yte, ypred)

            # Grid-level metrics (aggregate samples to grid cells)
            te_df = df_use.iloc[te].copy()
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

    cv_pred, cv_folds = run_group_cv(df_with_ms, X_final, y_train, groups, sample_weights, RF_PARAMS, N_FOLDS)
    outp.mkdir(parents=True, exist_ok=True)
    cv_folds.to_csv(outp/'cv_group_folds.csv', index=False)

    # Final model
    print("\nTraining final Random Forest model on all data...")
    final_model = RandomForestRegressor(**RF_PARAMS)
    final_model.fit(X_final, y_train, sample_weight=sample_weights)
    model_path = outp / 'rf_disagg_model.pkl'
    joblib.dump(final_model, model_path)
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

    # Final metrics summary
    sample_r2_cv = r2_score(y_train, cv_pred)
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
    df_with_ms.to_csv(outp/'sample_predictions.csv', index=False)
    print(f"\nSaved sample-level predictions to: {outp/'sample_predictions.csv'}")
    grid_agg.to_csv(outp/'grid_predictions.csv', index=False)
    print(f"Saved grid-level predictions to: {outp/'grid_predictions.csv'}")

    # ----------------- DHS External Validation -----------------
    dhs_summary = run_dhs_external_validation(
        model=final_model,
        X_all=X_final,
        df_all=df_with_ms,
        feature_cols=feature_cols,
        output_dir=outp,
    )
    if dhs_summary:
        print(f"\n=== COMBINED SUMMARY ===")
        print(f"Internal CV — Sample R²: {sample_r2_cv:.4f}, Grid R²: {grid_r2_cv:.4f}")
        print(f"DHS External — Spearman ρ: {dhs_summary['spearman_rho']:+.4f} "
              f"(p={dhs_summary['spearman_p']:.4f}), "
              f"Pearson r: {dhs_summary['pearson_r']:+.4f} "
              f"(p={dhs_summary['pearson_p']:.4f}), "
              f"n={dhs_summary['n_clusters']}")


if __name__ == "__main__":
    disaggregate_pipeline(CSV_PATH, OUTPUT_DIR)

