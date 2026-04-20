"""
Diagnostic script: Identify whether elevated poverty predictions at barangay
borders are caused by model predictions or rendering.

Approach:
  1. Load grid cells + predictions + barangay boundaries
  2. Classify each cell as border (touches ≥2 barangays) or interior
  3. Compare CatBoost/RF/CNN predictions for border vs interior cells
  4. Check raw vs scaled predictions if available
"""
import warnings
warnings.filterwarnings("ignore")

from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
SHAPE_DIR = ASSETS_DIR / "shapefile"
OUTPUT_DIR = PROJECT_ROOT / "output"

# ── 1. Load data ─────────────────────────────────────────────────────────────
print("=" * 70)
print("BORDER EFFECT DIAGNOSTIC")
print("=" * 70)

# Grid geometry
grid_gpkg = DATA_DIR / "grid_1km_all.gpkg"
grid_gdf = gpd.read_file(grid_gpkg)
print(f"\nGrid cells loaded: {len(grid_gdf)}")
print(f"  CRS: {grid_gdf.crs}")
print(f"  Columns: {grid_gdf.columns.tolist()}")

# Convert cell_id to grid_id
def cell_id_to_grid_id(cell_id):
    try:
        parts = str(cell_id).split('_')
        if len(parts) == 3 and parts[0] == 'cell':
            return f"{int(parts[1])}_{int(parts[2])}"
    except:
        pass
    return str(cell_id)

if 'cell_id' in grid_gdf.columns:
    grid_gdf['grid_id'] = grid_gdf['cell_id'].apply(cell_id_to_grid_id)

# Predictions
preds_csv = DATA_DIR / "complete_grid_predictions.csv"
preds_df = pd.read_csv(preds_csv)
print(f"\nPredictions loaded: {len(preds_df)}")
print(f"  Columns: {preds_df.columns.tolist()}")
print(f"  CatBoost range: [{preds_df['pred_scaled_catboost'].min():.4f}, {preds_df['pred_scaled_catboost'].max():.4f}]")
print(f"  RF range: [{preds_df['pred_scaled_rf'].min():.4f}, {preds_df['pred_scaled_rf'].max():.4f}]")

# CNN predictions
cnn_csv = DATA_DIR / "all_cells_predictions_1km.csv"
cnn_df = pd.read_csv(cnn_csv)
print(f"\nCNN predictions loaded: {len(cnn_df)}")
if 'cell_id' in cnn_df.columns:
    cnn_df['grid_id'] = cnn_df['cell_id'].apply(cell_id_to_grid_id)
if 'predicted_poverty' in cnn_df.columns:
    print(f"  CNN range: [{cnn_df['predicted_poverty'].min():.4f}, {cnn_df['predicted_poverty'].max():.4f}]")

# Barangay boundaries
bnd_path = SHAPE_DIR / "zc04AdminBoundaries.shp"
bnd_gdf = gpd.read_file(bnd_path)
print(f"\nBarangay boundaries loaded: {len(bnd_gdf)} features")

# Ensure same CRS
if grid_gdf.crs != bnd_gdf.crs:
    bnd_gdf = bnd_gdf.to_crs(grid_gdf.crs)

# Identify barangay name column
bgy_col = None
for col in ['adm4_en', 'barangay_name_clean']:
    if col in bnd_gdf.columns:
        bgy_col = col
        break
if bgy_col is None:
    for col in bnd_gdf.columns:
        if 'brgy' in col.lower() or 'barangay' in col.lower():
            bgy_col = col
            break
print(f"  Barangay name column: {bgy_col}")

# ── 2. Classify cells as border or interior ──────────────────────────────────
print("\n" + "=" * 70)
print("CLASSIFYING GRID CELLS: BORDER vs INTERIOR")
print("=" * 70)

# Spatial join: for each grid cell, find ALL barangays it intersects
grid_gdf_wgs = grid_gdf.to_crs(epsg=4326) if grid_gdf.crs.to_epsg() != 4326 else grid_gdf
bnd_wgs = bnd_gdf.to_crs(epsg=4326) if bnd_gdf.crs.to_epsg() != 4326 else bnd_gdf

# Keep only valid barangay polygons
bnd_valid = bnd_wgs[bnd_wgs[bgy_col].notna()].copy()
# Remove any full-extent bounding boxes
minx, miny, maxx, maxy = bnd_valid.total_bounds
tol = 1e-6
extent_mask = bnd_valid.geometry.apply(lambda g: (
    abs(g.bounds[0] - minx) < tol and abs(g.bounds[1] - miny) < tol and
    abs(g.bounds[2] - maxx) < tol and abs(g.bounds[3] - maxy) < tol
))
if extent_mask.any():
    bnd_valid = bnd_valid[~extent_mask].copy()
    print(f"  Removed {extent_mask.sum()} full-extent bounding box features")

print(f"  Valid barangay polygons: {len(bnd_valid)}")

# Spatial join - how many barangays does each grid cell touch?
joined = gpd.sjoin(grid_gdf_wgs, bnd_valid[[bgy_col, 'geometry']], how='left', predicate='intersects')
barangays_per_cell = joined.groupby('grid_id')[bgy_col].nunique().reset_index()
barangays_per_cell.columns = ['grid_id', 'n_barangays_touched']

# Also count cells with no barangay at all
no_brgy_mask = joined[bgy_col].isna()
no_brgy_cells = set(joined.loc[no_brgy_mask, 'grid_id'].unique())

# Classify
barangays_per_cell['cell_type'] = 'interior'
barangays_per_cell.loc[barangays_per_cell['n_barangays_touched'] >= 2, 'cell_type'] = 'border'
barangays_per_cell.loc[barangays_per_cell['n_barangays_touched'] == 0, 'cell_type'] = 'unassigned'
# Also mark cells that touch exactly 1 barangay but are at the edge (near unassigned cells)

print(f"\nCell classification:")
print(barangays_per_cell['cell_type'].value_counts().to_string())
print(f"\nBarangays touched distribution:")
print(barangays_per_cell['n_barangays_touched'].value_counts().sort_index().to_string())

# ── 3. Merge predictions with cell classification ────────────────────────────
print("\n" + "=" * 70)
print("COMPARING PREDICTIONS: BORDER vs INTERIOR CELLS")
print("=" * 70)

analysis = barangays_per_cell.merge(preds_df, on='grid_id', how='inner')
print(f"\nMatched cells with predictions: {len(analysis)}")

# Also merge CNN
if 'grid_id' in cnn_df.columns and 'predicted_poverty' in cnn_df.columns:
    analysis = analysis.merge(cnn_df[['grid_id', 'predicted_poverty']], on='grid_id', how='left')

for model_col, model_name in [
    ('pred_scaled_catboost', 'CatBoost'),
    ('pred_scaled_rf', 'Random Forest'),
    ('predicted_poverty', 'CNN'),
]:
    if model_col not in analysis.columns:
        continue
    
    print(f"\n--- {model_name} ---")
    valid = analysis[analysis[model_col].notna()]
    
    for ctype in ['interior', 'border', 'unassigned']:
        subset = valid[valid['cell_type'] == ctype]
        if len(subset) == 0:
            continue
        vals = subset[model_col]
        print(f"  {ctype:12s}: n={len(subset):4d}  "
              f"mean={vals.mean():.4f}  median={vals.median():.4f}  "
              f"std={vals.std():.4f}  "
              f"[{vals.min():.4f}, {vals.max():.4f}]")
    
    # Statistical test: border vs interior
    border_vals = valid.loc[valid['cell_type'] == 'border', model_col].values
    interior_vals = valid.loc[valid['cell_type'] == 'interior', model_col].values
    
    if len(border_vals) > 5 and len(interior_vals) > 5:
        from scipy import stats
        t_stat, p_value = stats.ttest_ind(border_vals, interior_vals, equal_var=False)
        diff = border_vals.mean() - interior_vals.mean()
        print(f"  Border - Interior diff: {diff:+.4f}  (t={t_stat:.2f}, p={p_value:.4f})")

# ── 4. Check if raw (pre-scaling) predictions also show the effect ───────────
print("\n" + "=" * 70)
print("CHECKING RAW vs PROCESSED PREDICTIONS")
print("=" * 70)

# Check CatBoost raw predictions
cat_grid_csv = OUTPUT_DIR / "catBoost" / "geospatial_disagg" / "grid_predictions.csv"
rf_grid_csv = OUTPUT_DIR / "rf" / "geospatial_disagg" / "grid_predictions.csv"

for csv_path, model_name in [(cat_grid_csv, 'CatBoost'), (rf_grid_csv, 'RF')]:
    if not csv_path.exists():
        print(f"\n{model_name}: grid_predictions.csv not found at {csv_path}")
        continue
    
    raw_df = pd.read_csv(csv_path)
    print(f"\n--- {model_name} raw grid_predictions.csv ---")
    print(f"  Columns: {raw_df.columns.tolist()}")
    print(f"  Rows: {len(raw_df)}")
    
    # Normalize grid IDs
    if 'grid_cell_id' in raw_df.columns and 'grid_id' not in raw_df.columns:
        raw_df['grid_id'] = raw_df['grid_cell_id'].apply(cell_id_to_grid_id)
    
    # Find prediction column
    pred_col = None
    for c in ['pred_raw', 'pred', 'pred_scaled']:
        if c in raw_df.columns:
            pred_col = c
            break
    
    if pred_col is None:
        print(f"  No prediction column found")
        continue
    
    print(f"  Prediction column: {pred_col}")
    print(f"  Range: [{raw_df[pred_col].min():.4f}, {raw_df[pred_col].max():.4f}]")
    
    # Merge with cell classification
    raw_analysis = barangays_per_cell.merge(raw_df, on='grid_id', how='inner')
    
    for ctype in ['interior', 'border', 'unassigned']:
        subset = raw_analysis[raw_analysis['cell_type'] == ctype]
        if len(subset) == 0:
            continue
        vals = subset[pred_col].dropna()
        if len(vals) == 0:
            continue
        print(f"  {ctype:12s}: n={len(vals):4d}  "
              f"mean={vals.mean():.4f}  median={vals.median():.4f}  "
              f"std={vals.std():.4f}")
    
    border_vals = raw_analysis.loc[raw_analysis['cell_type'] == 'border', pred_col].dropna().values
    interior_vals = raw_analysis.loc[raw_analysis['cell_type'] == 'interior', pred_col].dropna().values
    
    if len(border_vals) > 5 and len(interior_vals) > 5:
        from scipy import stats
        t_stat, p_value = stats.ttest_ind(border_vals, interior_vals, equal_var=False)
        diff = border_vals.mean() - interior_vals.mean()
        print(f"  Border - Interior diff: {diff:+.4f}  (t={t_stat:.2f}, p={p_value:.4f})")

# ── 5. Analyze spatial interpolation / CNN backfill contribution ─────────────
print("\n" + "=" * 70)
print("CHECKING SPATIAL INTERPOLATION / CNN BACKFILL EFFECTS")
print("=" * 70)

# Cells in complete_grid_predictions that are NOT in the raw catboost output
# → these were filled by spatial interpolation or CNN backfill
if cat_grid_csv.exists():
    raw_cat = pd.read_csv(cat_grid_csv)
    if 'grid_cell_id' in raw_cat.columns and 'grid_id' not in raw_cat.columns:
        raw_cat['grid_id'] = raw_cat['grid_cell_id'].apply(cell_id_to_grid_id)
    
    # Also load unlabeled predictions
    unl_cat_csv = OUTPUT_DIR / "catBoost" / "geospatial_disagg" / "unlabeled_grid_predictions.csv"
    if unl_cat_csv.exists():
        unl_cat = pd.read_csv(unl_cat_csv)
        if 'grid_cell_id' in unl_cat.columns and 'grid_id' not in unl_cat.columns:
            unl_cat['grid_id'] = unl_cat['grid_cell_id'].apply(cell_id_to_grid_id)
        all_model_ids = set(raw_cat['grid_id'].tolist() + unl_cat['grid_id'].tolist())
    else:
        all_model_ids = set(raw_cat['grid_id'].tolist())
    
    complete_ids = set(preds_df['grid_id'].tolist())
    interpolated_ids = complete_ids - all_model_ids
    
    print(f"\nCatBoost prediction sources:")
    print(f"  From model (labeled): {len(raw_cat)}")
    if unl_cat_csv.exists():
        print(f"  From model (unlabeled): {len(unl_cat)}")
    print(f"  Total in complete_grid_predictions: {len(preds_df)}")
    print(f"  Filled by interpolation/backfill: {len(interpolated_ids)}")
    
    # Check if interpolated cells are disproportionately border cells
    if len(interpolated_ids) > 0:
        interp_analysis = barangays_per_cell[barangays_per_cell['grid_id'].isin(interpolated_ids)]
        print(f"\n  Interpolated cells by type:")
        print(f"  {interp_analysis['cell_type'].value_counts().to_string()}")

# ── 6. Analyze adjacent cell prediction differences at boundaries ────────────
print("\n" + "=" * 70)
print("PREDICTION DISCONTINUITIES AT BARANGAY BOUNDARIES")
print("=" * 70)

# For each pair of adjacent cells belonging to different barangays,
# compute the prediction difference
# Get primary barangay assignment for each cell (most area overlap)
primary_brgy = joined.drop_duplicates(subset='grid_id', keep='first')[['grid_id', bgy_col]].copy()
primary_brgy.columns = ['grid_id', 'primary_barangay']

analysis_with_brgy = analysis.merge(primary_brgy, on='grid_id', how='left')

# Build adjacency from grid structure (cells sharing an edge)
if 'x_idx_x' in grid_gdf.columns and 'y_idx_x' in grid_gdf.columns:
    x_col, y_col = 'x_idx_x', 'y_idx_x'
elif 'x_idx' in grid_gdf.columns and 'y_idx' in grid_gdf.columns:
    x_col, y_col = 'x_idx', 'y_idx'
else:
    # Parse from grid_id
    def parse_grid_id(gid):
        parts = str(gid).split('_')
        return int(parts[0]), int(parts[1])
    
    coords = analysis_with_brgy['grid_id'].apply(parse_grid_id)
    analysis_with_brgy['x_idx'] = [c[0] for c in coords]
    analysis_with_brgy['y_idx'] = [c[1] for c in coords]
    x_col, y_col = 'x_idx', 'y_idx'

# Build lookup
cell_lookup = {}
for _, row in analysis_with_brgy.iterrows():
    try:
        x, y = int(row[x_col]), int(row[y_col])
        cell_lookup[(x, y)] = row
    except:
        continue

cross_boundary_diffs_cat = []
cross_boundary_diffs_rf = []
cross_boundary_diffs_cnn = []
same_boundary_diffs_cat = []
same_boundary_diffs_rf = []
same_boundary_diffs_cnn = []

for (x, y), row in cell_lookup.items():
    for dx, dy in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
        neighbor = cell_lookup.get((x + dx, y + dy))
        if neighbor is None:
            continue
        
        brgy1 = row.get('primary_barangay')
        brgy2 = neighbor.get('primary_barangay')
        
        if pd.isna(brgy1) or pd.isna(brgy2):
            continue
        
        is_cross = brgy1 != brgy2
        
        for col, cross_list, same_list in [
            ('pred_scaled_catboost', cross_boundary_diffs_cat, same_boundary_diffs_cat),
            ('pred_scaled_rf', cross_boundary_diffs_rf, same_boundary_diffs_rf),
            ('predicted_poverty', cross_boundary_diffs_cnn, same_boundary_diffs_cnn),
        ]:
            v1 = row.get(col)
            v2 = neighbor.get(col)
            if pd.notna(v1) and pd.notna(v2):
                diff = abs(float(v1) - float(v2))
                if is_cross:
                    cross_list.append(diff)
                else:
                    same_list.append(diff)

for model_name, cross_diffs, same_diffs in [
    ('CatBoost', cross_boundary_diffs_cat, same_boundary_diffs_cat),
    ('RF', cross_boundary_diffs_rf, same_boundary_diffs_rf),
    ('CNN', cross_boundary_diffs_cnn, same_boundary_diffs_cnn),
]:
    if not cross_diffs and not same_diffs:
        continue
    
    print(f"\n--- {model_name} adjacent-cell prediction differences ---")
    if same_diffs:
        print(f"  Same barangay:  n={len(same_diffs):4d}  mean_abs_diff={np.mean(same_diffs):.4f}  median={np.median(same_diffs):.4f}")
    if cross_diffs:
        print(f"  Cross boundary: n={len(cross_diffs):4d}  mean_abs_diff={np.mean(cross_diffs):.4f}  median={np.median(cross_diffs):.4f}")
    if same_diffs and cross_diffs:
        ratio = np.mean(cross_diffs) / (np.mean(same_diffs) + 1e-8)
        print(f"  Cross/Same ratio: {ratio:.2f}x")

# ── 7. Summary ───────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("DIAGNOSTIC SUMMARY")
print("=" * 70)

print("""
If border cells have significantly higher mean predictions than interior 
cells for CatBoost/RF but NOT for CNN, the effect is in the model predictions.

If cross-boundary adjacent-cell differences are much larger than 
same-barangay differences for CatBoost/RF but not CNN, this confirms 
the discontinuity is at barangay boundaries.

Root cause candidates:
  1. Per-barangay target assignment creates discontinuities in training data
  2. Multi-scale spatial aggregates (500/1000/2000m) blend cross-boundary signals
  3. Spatial interpolation/CNN backfill for missing cells at boundaries
""")
