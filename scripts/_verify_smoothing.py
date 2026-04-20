"""Verify that the boundary smoothing reduces cross-boundary prediction jumps."""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
import geopandas as gpd

# Replicate what app.py does
grid_df = gpd.read_file(r"data\grid_1km_all.gpkg")
preds_df = pd.read_csv(r"data\complete_grid_predictions.csv")

def cell_id_to_grid_id(cell_id):
    try:
        parts = str(cell_id).split('_')
        if len(parts) == 3 and parts[0] == 'cell':
            return f"{int(parts[1])}_{int(parts[2])}"
    except:
        pass
    return None

grid_df['grid_id'] = grid_df['cell_id'].apply(cell_id_to_grid_id)

merged = (
    grid_df.merge(preds_df, on="grid_id", how="inner")
    .dropna(subset=["pred_scaled_catboost", "pred_scaled_rf"])
    .reset_index(drop=True)
)

print(f"Merged cells: {len(merged)}")

# Import the smoothing function
from src.server.app import _smooth_boundary_predictions

# BEFORE smoothing
before = merged.copy()

# AFTER smoothing
after = _smooth_boundary_predictions(
    merged,
    pred_cols=["pred_scaled_catboost", "pred_scaled_rf"],
    blend=0.3,
)

# Compare: compute cross-boundary jumps before and after
def parse_gid(g):
    parts = str(g).split('_')
    return int(parts[0]), int(parts[1])

bnd = gpd.read_file(r"assets\shapefile\zc04AdminBoundaries.shp").to_crs(epsg=4326)
bnd_valid = bnd[bnd['adm4_en'].notna()]
joined = gpd.sjoin(grid_df.to_crs(4326), bnd_valid[['adm4_en', 'geometry']], how='left', predicate='intersects')
primary = joined.drop_duplicates(subset='grid_id', keep='first')[['grid_id', 'adm4_en']]

for label, df in [("BEFORE smoothing", before), ("AFTER smoothing", after)]:
    analysis = df.merge(primary, on='grid_id', how='left')
    analysis['xi'] = [int(g.split('_')[0]) for g in analysis['grid_id']]
    analysis['yi'] = [int(g.split('_')[1]) for g in analysis['grid_id']]
    
    lk = {}
    for _, r in analysis.iterrows():
        lk[(int(r['xi']), int(r['yi']))] = r
    
    n_brgy = joined.groupby('grid_id')['adm4_en'].nunique().reset_index()
    n_brgy.columns = ['grid_id', 'n_brgys']
    n_brgy['is_border'] = n_brgy['n_brgys'] >= 2
    
    with_type = df.merge(n_brgy[['grid_id', 'is_border']], on='grid_id', how='left')
    with_type = with_type.merge(primary, on='grid_id', how='left')
    
    # Within-barangay border vs interior
    brgy_diffs = []
    for brgy, sub in with_type.groupby('adm4_en'):
        if pd.isna(brgy):
            continue
        interior = sub[~sub['is_border']]['pred_scaled_catboost']
        border = sub[sub['is_border']]['pred_scaled_catboost']
        if len(interior) > 0 and len(border) > 0:
            brgy_diffs.append(border.mean() - interior.mean())
    
    cross_diffs = []
    same_diffs = []
    for (x, y), row in lk.items():
        for dx, dy in [(1, 0), (0, 1)]:
            nb = lk.get((x+dx, y+dy))
            if nb is None:
                continue
            b1, b2 = row.get('adm4_en'), nb.get('adm4_en')
            if pd.isna(b1) or pd.isna(b2):
                continue
            diff = abs(float(row['pred_scaled_catboost']) - float(nb['pred_scaled_catboost']))
            if b1 != b2:
                cross_diffs.append(diff)
            else:
                same_diffs.append(diff)
    
    print(f"\n{label}:")
    print(f"  CatBoost overall mean: {df['pred_scaled_catboost'].mean()*100:.2f}%")
    print(f"  Same-brgy adj diff: {np.mean(same_diffs)*100:.3f}pp")
    print(f"  Cross-boundary adj diff: {np.mean(cross_diffs)*100:.3f}pp")
    print(f"  Cross/Same ratio: {np.mean(cross_diffs)/(np.mean(same_diffs)+1e-8):.2f}x")
    print(f"  Mean within-brgy border-interior diff: {np.mean(brgy_diffs)*100:+.2f}pp")
    print(f"  Barangays where border > interior: {sum(1 for d in brgy_diffs if d > 0)}/{len(brgy_diffs)}")
