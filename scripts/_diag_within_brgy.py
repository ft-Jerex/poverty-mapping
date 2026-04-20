"""Check: within each barangay, do border cells predict higher than interior cells?"""
import pandas as pd
import numpy as np
import geopandas as gpd

grid = gpd.read_file(r"data\grid_1km_all.gpkg")
grid['grid_id'] = grid['cell_id'].apply(
    lambda c: f"{int(str(c).split('_')[1])}_{int(str(c).split('_')[2])}" if len(str(c).split('_'))==3 else str(c))

bnd = gpd.read_file(r"assets\shapefile\zc04AdminBoundaries.shp").to_crs(epsg=4326)
bnd_valid = bnd[bnd['adm4_en'].notna()]
joined = gpd.sjoin(grid.to_crs(4326), bnd_valid[['adm4_en', 'geometry']], how='left', predicate='intersects')

# Count barangays per cell -> border classification
n_brgy = joined.groupby('grid_id')['adm4_en'].nunique().reset_index()
n_brgy.columns = ['grid_id', 'n_brgys']
n_brgy['is_border'] = n_brgy['n_brgys'] >= 2

# Primary barangay assignment
primary = joined.drop_duplicates(subset='grid_id', keep='first')[['grid_id', 'adm4_en']]

# Load predictions (use current model output)
preds = pd.read_csv(r"data\grid_predictions_comparison.csv")

analysis = preds[['grid_id', 'pred_scaled_catboost', 'pred_scaled_rf']].merge(
    n_brgy, on='grid_id', how='inner'
).merge(primary, on='grid_id', how='left')

# For each barangay: compare border vs interior cells
print(f"{'Barangay':>30s} {'Interior':>10s} {'Border':>10s} {'Diff':>8s} {'n_int':>6s} {'n_brd':>6s}")
print("-" * 80)

brgy_diffs = []
for brgy, sub in analysis.groupby('adm4_en'):
    if pd.isna(brgy):
        continue
    interior = sub[~sub['is_border']]['pred_scaled_catboost']
    border = sub[sub['is_border']]['pred_scaled_catboost']
    
    if len(interior) == 0 or len(border) == 0:
        continue
    
    diff = border.mean() - interior.mean()
    brgy_diffs.append({
        'barangay': brgy,
        'interior_mean': interior.mean(),
        'border_mean': border.mean(),
        'diff': diff,
        'n_interior': len(interior),
        'n_border': len(border),
    })

brgy_diffs = pd.DataFrame(brgy_diffs).sort_values('diff', ascending=False)

# Show barangays where border > interior
print("\nBarangays where BORDER cells predict HIGHER than interior:")
higher = brgy_diffs[brgy_diffs['diff'] > 0]
for _, r in higher.head(15).iterrows():
    print(f"  {r['barangay']:>30s}  int={r['interior_mean']*100:5.1f}%  brd={r['border_mean']*100:5.1f}%  diff={r['diff']*100:+5.1f}pp  n_int={int(r['n_interior'])}  n_brd={int(r['n_border'])}")

print(f"\nBarangays where BORDER cells predict LOWER than interior:")
lower = brgy_diffs[brgy_diffs['diff'] < 0]
for _, r in lower.head(15).iterrows():
    print(f"  {r['barangay']:>30s}  int={r['interior_mean']*100:5.1f}%  brd={r['border_mean']*100:5.1f}%  diff={r['diff']*100:+5.1f}pp  n_int={int(r['n_interior'])}  n_brd={int(r['n_border'])}")

print(f"\nSummary:")
print(f"  Barangays with border > interior: {len(higher)}")
print(f"  Barangays with border < interior: {len(lower)}")
print(f"  Mean within-barangay diff: {brgy_diffs['diff'].mean()*100:+.2f}pp")
print(f"  Median within-barangay diff: {brgy_diffs['diff'].median()*100:+.2f}pp")
