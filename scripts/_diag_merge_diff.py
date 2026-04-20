"""Quick check: how does merge_predictions modify CatBoost/RF predictions?"""
import pandas as pd

cat = pd.read_csv(r"output\catBoost\geospatial_disagg\grid_predictions.csv")
complete = pd.read_csv(r"data\complete_grid_predictions.csv")

print(f"CatBoost output: {len(cat)} rows, {cat['grid_id'].nunique()} unique grid_ids")
print(f"Complete predictions: {len(complete)} rows")

# Merge to compare
merged = cat[['grid_id', 'pred_scaled_catboost']].merge(
    complete[['grid_id', 'pred_scaled_catboost', 'barangay_name_clean']],
    on='grid_id', suffixes=('_model', '_final'))

merged['diff'] = merged['pred_scaled_catboost_model'] - merged['pred_scaled_catboost_final']
merged['abs_diff'] = merged['diff'].abs()

print(f"\nMatched cells: {len(merged)}")
print(f"Max abs diff: {merged['abs_diff'].max()*100:.1f}pp")
print(f"Mean abs diff: {merged['abs_diff'].mean()*100:.1f}pp")

# Show top 15 differences
print("\nTop 15 largest changes from model output -> final:")
top = merged.nlargest(15, 'abs_diff')
for _, r in top.iterrows():
    brgy = str(r['barangay_name_clean'])[:25]
    print(f"  {r['grid_id']:>8s} {brgy:>25s}  model={r['pred_scaled_catboost_model']*100:5.1f}%  final={r['pred_scaled_catboost_final']*100:5.1f}%  change={r['diff']*100:+6.1f}pp")

# Direction of changes
print(f"\nDirection of changes:")
print(f"  Model > Final (prediction reduced): {(merged['diff'] > 0.001).sum()}")
print(f"  Model < Final (prediction increased): {(merged['diff'] < -0.001).sum()}")
print(f"  ~Same (<0.1pp): {(merged['abs_diff'] < 0.001).sum()}")

# Check: are the cells that got increased disproportionately border cells?
# Load grid geometry and barangay boundaries
import geopandas as gpd

grid = gpd.read_file(r"data\grid_1km_all.gpkg")
grid['grid_id'] = grid['cell_id'].apply(
    lambda c: f"{int(str(c).split('_')[1])}_{int(str(c).split('_')[2])}" if len(str(c).split('_'))==3 else str(c))

bnd = gpd.read_file(r"assets\shapefile\zc04AdminBoundaries.shp").to_crs(epsg=4326)
bnd_valid = bnd[bnd['adm4_en'].notna()]

joined = gpd.sjoin(grid.to_crs(4326), bnd_valid[['adm4_en', 'geometry']], how='left', predicate='intersects')
n_brgy_per_cell = joined.groupby('grid_id')['adm4_en'].nunique().reset_index()
n_brgy_per_cell.columns = ['grid_id', 'n_barangays']

merged2 = merged.merge(n_brgy_per_cell, on='grid_id', how='left')
merged2['is_border'] = merged2['n_barangays'] >= 2

print("\nAvg prediction change by cell type:")
for is_b, label in [(False, 'Interior'), (True, 'Border')]:
    subset = merged2[merged2['is_border'] == is_b]
    print(f"  {label}: n={len(subset)}, mean change={subset['diff'].mean()*100:+.2f}pp, "
          f"mean abs change={subset['abs_diff'].mean()*100:.2f}pp")

# Check by barangay: which barangays have the largest prediction shifts?
print("\nBarangay-level prediction shifts (top 10):")
brgy_shifts = merged.groupby('barangay_name_clean').agg(
    n_cells=('diff', 'count'),
    mean_change=('diff', 'mean'),
    mean_abs_change=('abs_diff', 'mean'),
).reset_index()
brgy_shifts = brgy_shifts.sort_values('mean_abs_change', ascending=False).head(10)
for _, r in brgy_shifts.iterrows():
    print(f"  {str(r['barangay_name_clean']):>30s}: n={int(r['n_cells']):3d}  "
          f"mean_change={r['mean_change']*100:+5.1f}pp  "
          f"mean_abs_change={r['mean_abs_change']*100:5.1f}pp")
