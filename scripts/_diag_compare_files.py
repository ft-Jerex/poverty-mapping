"""Compare border effects between the stale and current prediction files."""
import pandas as pd
import numpy as np
import geopandas as gpd

# Load grid + boundaries
grid = gpd.read_file(r"data\grid_1km_all.gpkg")
grid['grid_id'] = grid['cell_id'].apply(
    lambda c: f"{int(str(c).split('_')[1])}_{int(str(c).split('_')[2])}" if len(str(c).split('_'))==3 else str(c))

bnd = gpd.read_file(r"assets\shapefile\zc04AdminBoundaries.shp").to_crs(epsg=4326)
bnd_valid = bnd[bnd['adm4_en'].notna()]
joined = gpd.sjoin(grid.to_crs(4326), bnd_valid[['adm4_en', 'geometry']], how='left', predicate='intersects')
n_brgy = joined.groupby('grid_id')['adm4_en'].nunique().reset_index()
n_brgy.columns = ['grid_id', 'n_brgys']
n_brgy['is_border'] = n_brgy['n_brgys'] >= 2
primary = joined.drop_duplicates(subset='grid_id', keep='first')[['grid_id', 'adm4_en']]

# Load all three prediction files
stale = pd.read_csv(r"data\complete_grid_predictions.csv")   # what app.py actually uses
current = pd.read_csv(r"data\grid_predictions_comparison.csv")  # from merge pipeline
model_out = pd.read_csv(r"output\catBoost\geospatial_disagg\grid_predictions.csv")  # raw model

for label, df, col in [
    ("STALE (complete_grid_predictions.csv)", stale, 'pred_scaled_catboost'),
    ("CURRENT (grid_predictions_comparison.csv)", current, 'pred_scaled_catboost'),
    ("MODEL OUTPUT (catboost grid_predictions.csv)", model_out, 'pred_scaled_catboost'),
]:
    if col not in df.columns:
        print(f"\n{label}: column {col} not found")
        continue
    
    analysis = n_brgy.merge(df[['grid_id', col]], on='grid_id', how='inner')
    analysis = analysis.merge(primary, on='grid_id', how='left')
    
    border = analysis[analysis['is_border']]
    interior = analysis[~analysis['is_border']]
    
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"  Total cells: {len(analysis)}")
    print(f"  Interior: n={len(interior)}, mean={interior[col].mean()*100:.1f}%, std={interior[col].std()*100:.1f}%")
    print(f"  Border:   n={len(border)}, mean={border[col].mean()*100:.1f}%, std={border[col].std()*100:.1f}%")
    print(f"  Border-Interior diff: {(border[col].mean()-interior[col].mean())*100:+.1f}pp")
    
    # Cross-boundary jumps
    analysis['xi'] = [int(g.split('_')[0]) for g in analysis['grid_id']]
    analysis['yi'] = [int(g.split('_')[1]) for g in analysis['grid_id']]
    lk = {}
    for _, r in analysis.iterrows():
        lk[(int(r['xi']), int(r['yi']))] = r
    
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
            v1, v2 = float(row[col]), float(nb[col])
            diff = abs(v1 - v2)
            if b1 != b2:
                cross_diffs.append(diff)
            else:
                same_diffs.append(diff)
    
    if same_diffs and cross_diffs:
        print(f"  Same-barangay adj diff: mean={np.mean(same_diffs)*100:.2f}pp (n={len(same_diffs)})")
        print(f"  Cross-boundary adj diff: mean={np.mean(cross_diffs)*100:.2f}pp (n={len(cross_diffs)})")
        print(f"  Cross/Same ratio: {np.mean(cross_diffs)/np.mean(same_diffs):.2f}x")
