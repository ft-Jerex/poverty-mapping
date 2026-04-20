"""
Prepare DHS 2022 survey data for external validation.

Extracts household wealth factor scores (hv271) and GPS cluster locations
from the DHS zip, aggregates to cluster level, spatially matches clusters
to the nearest grid cells, and saves validation-ready CSVs.

Outputs:
    data/dhs_cluster_validation.csv  - cluster-level DHS wealth data + GPS
    data/dhs_grid_matched.csv        - DHS clusters matched to nearest grid cells
"""

import warnings
warnings.filterwarnings("ignore")

import os
import zipfile
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import cKDTree

# -------------------------
# CONFIG
# -------------------------
BASE_DIR = Path(__file__).resolve().parent          # scripts/
PROJECT_ROOT = BASE_DIR.parent                      # project root
DHS_ZIP = PROJECT_ROOT / 'PH_2022_DHS_04182026_1956_231319.zip'
GRID_CSV = PROJECT_ROOT / 'assets' / 'grid_with_comprehensive_data.csv'
OUTPUT_DIR = PROJECT_ROOT / 'data'
MAX_MATCH_DISTANCE_DEG = 0.5   # ~55 km — generous bounding box for nearby clusters
MATCH_WARNING_M = 2000         # flag clusters whose nearest grid cell is >2 km away


def haversine_m(lon1, lat1, lon2, lat2):
    """Vectorised haversine distance in metres."""
    R = 6_371_000
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(a))


# -------------------------
# 1. EXTRACT DHS CLUSTER-LEVEL DATA
# -------------------------
def extract_dhs_clusters(zip_path: Path) -> pd.DataFrame:
    """Extract household wealth scores and GPS, aggregate to cluster level."""
    print("=== Extracting DHS data from zip ===")

    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zip_path) as z:
            # Extract household recode
            for name in z.namelist():
                if name.startswith('PHHR82DT/') or name.startswith('PHGE81FL/'):
                    z.extract(name, td)

        # --- Household recode ---
        dta_path = os.path.join(td, 'PHHR82DT', 'PHHR82FL.DTA')
        print(f"  Reading household recode: {dta_path}")
        hh = pd.read_stata(dta_path, convert_categoricals=False,
                           columns=['hv001', 'hv002', 'hv270', 'hv271', 'hv270a', 'hv271a'])

        # hv001 = cluster id, hv271 = wealth index factor score (continuous)
        cluster_agg = hh.groupby('hv001').agg(
            mean_wealth_score=('hv271', 'mean'),
            median_wealth_score=('hv271', 'median'),
            std_wealth_score=('hv271', 'std'),
            pct_poorest_quintile=('hv270', lambda x: (x == 1).mean()),
            pct_bottom2_quintiles=('hv270', lambda x: (x <= 2).mean()),
            n_households=('hv002', 'count'),
        ).reset_index().rename(columns={'hv001': 'cluster_id'})

        print(f"  Aggregated {len(hh)} households → {len(cluster_agg)} clusters")

        # --- GPS shapefile ---
        gps_dir = os.path.join(td, 'PHGE81FL')
        print(f"  Reading GPS shapefile: {gps_dir}")
        gdf = gpd.read_file(gps_dir)

        gps = gdf[['DHSCLUST', 'LATNUM', 'LONGNUM', 'ALT_DEM', 'URBAN_RURA', 'ADM1NAME']].copy()
        gps.columns = ['cluster_id', 'lat', 'lon', 'alt_dem', 'urban_rural', 'adm1_name']
        gps['cluster_id'] = gps['cluster_id'].astype(int)

        # Merge
        merged = cluster_agg.merge(gps, on='cluster_id', how='inner')
        print(f"  Merged: {len(merged)} clusters with both HH data and GPS")

        # Drop clusters with missing coordinates (lat=0, lon=0 is DHS convention for suppressed GPS)
        valid_mask = (merged['lat'].abs() > 0.01) & (merged['lon'].abs() > 0.01)
        dropped = (~valid_mask).sum()
        if dropped > 0:
            print(f"  Dropped {dropped} clusters with suppressed GPS (lat/lon ~ 0)")
        merged = merged[valid_mask].reset_index(drop=True)

    return merged


# -------------------------
# 2. SPATIAL MATCHING TO GRID
# -------------------------
def match_clusters_to_grid(clusters: pd.DataFrame, grid_csv: Path,
                           max_dist_deg: float = MAX_MATCH_DISTANCE_DEG) -> pd.DataFrame:
    """Match each DHS cluster to the nearest grid cell using cKDTree."""
    print("\n=== Spatial matching DHS clusters → grid cells ===")

    grid = pd.read_csv(grid_csv, usecols=['grid_id', 'lon', 'lat'])
    grid_lon_range = (grid['lon'].min(), grid['lon'].max())
    grid_lat_range = (grid['lat'].min(), grid['lat'].max())
    print(f"  Grid bounds: lon [{grid_lon_range[0]:.4f}, {grid_lon_range[1]:.4f}], "
          f"lat [{grid_lat_range[0]:.4f}, {grid_lat_range[1]:.4f}]")
    print(f"  Grid cells: {len(grid)}")

    # Filter DHS clusters to those within bounding box + buffer
    nearby_mask = (
        (clusters['lon'] >= grid_lon_range[0] - max_dist_deg) &
        (clusters['lon'] <= grid_lon_range[1] + max_dist_deg) &
        (clusters['lat'] >= grid_lat_range[0] - max_dist_deg) &
        (clusters['lat'] <= grid_lat_range[1] + max_dist_deg)
    )
    nearby = clusters[nearby_mask].copy().reset_index(drop=True)
    print(f"  DHS clusters within bounding box (±{max_dist_deg}°): {len(nearby)}")

    if len(nearby) == 0:
        print("  WARNING: No DHS clusters near the grid area!")
        return pd.DataFrame()

    # Build KD-tree on grid coordinates
    grid_coords = grid[['lon', 'lat']].values
    tree = cKDTree(grid_coords)

    # Query nearest grid cell for each DHS cluster
    cluster_coords = nearby[['lon', 'lat']].values
    distances_deg, indices = tree.query(cluster_coords, k=1)

    nearby['nearest_grid_id'] = grid.iloc[indices]['grid_id'].values
    nearby['nearest_grid_lon'] = grid.iloc[indices]['lon'].values
    nearby['nearest_grid_lat'] = grid.iloc[indices]['lat'].values

    # Compute actual distance in metres
    nearby['match_distance_m'] = haversine_m(
        nearby['lon'].values, nearby['lat'].values,
        nearby['nearest_grid_lon'].values, nearby['nearest_grid_lat'].values
    )

    # Flag high-distance matches
    nearby['match_confidence'] = np.where(
        nearby['match_distance_m'] <= MATCH_WARNING_M, 'high', 'low'
    )

    high_conf = (nearby['match_confidence'] == 'high').sum()
    low_conf = (nearby['match_confidence'] == 'low').sum()
    print(f"  High-confidence matches (≤{MATCH_WARNING_M}m): {high_conf}")
    print(f"  Low-confidence matches (>{MATCH_WARNING_M}m): {low_conf}")
    print(f"  Mean match distance: {nearby['match_distance_m'].mean():.0f} m")
    print(f"  Max match distance: {nearby['match_distance_m'].max():.0f} m")

    return nearby


# -------------------------
# MAIN
# -------------------------
def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Extract and aggregate DHS data
    clusters = extract_dhs_clusters(DHS_ZIP)
    cluster_path = OUTPUT_DIR / 'dhs_cluster_validation.csv'
    clusters.to_csv(cluster_path, index=False)
    print(f"\nSaved all DHS clusters to: {cluster_path}")
    print(f"  Total clusters: {len(clusters)}")
    print(f"  Wealth score range: [{clusters['mean_wealth_score'].min():.0f}, "
          f"{clusters['mean_wealth_score'].max():.0f}]")

    # Step 2: Match to grid
    matched = match_clusters_to_grid(clusters, GRID_CSV)
    if len(matched) > 0:
        matched_path = OUTPUT_DIR / 'dhs_grid_matched.csv'
        matched.to_csv(matched_path, index=False)
        print(f"\nSaved matched DHS-grid data to: {matched_path}")
        print(f"  Matched clusters: {len(matched)}")

        # Quick stats
        print(f"\n=== Matched cluster summary ===")
        print(f"  Urban: {(matched['urban_rural'] == 'U').sum()}, "
              f"Rural: {(matched['urban_rural'] == 'R').sum()}")
        print(f"  Regions: {matched['adm1_name'].value_counts().to_dict()}")
        print(f"  Mean wealth score: {matched['mean_wealth_score'].mean():.0f}")
        print(f"  Pct poorest quintile (mean): {matched['pct_poorest_quintile'].mean():.2%}")
    else:
        print("\nNo matched clusters found. Cannot proceed with external validation.")


if __name__ == '__main__':
    main()
