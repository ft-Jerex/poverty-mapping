import os
from pathlib import Path

import sys
import urllib.request
import datetime
import json
import xarray  # noqa: F401
import ee
import geopandas as gpd
import matplotlib.pyplot as plt  # noqa: F401
import numpy as np
import pandas as pd
import rasterio
import xarray
from IPython.display import display  # noqa: F401
from shapely.geometry import shape  # noqa: F401

# Try importing from gee_zc_grid_watcher, fall back to inline implementation
try:
    from gee_zc_grid_watcher import _get_credentials, EE_PROJECT_ID
except ImportError:
    # Inline implementation when gee_zc_grid_watcher is not available
    from google.oauth2 import service_account
    
    EE_PROJECT_ID = "ee-zc-povertymapping"
    
    # Look for service account file in multiple locations
    _POSSIBLE_SA_PATHS = [
        Path(__file__).resolve().parent / "env" / "ee-zc-povertymapping-0c4c39483d32.json",
        Path(__file__).resolve().parent.parent / "env" / "ee-zc-povertymapping-0c4c39483d32.json",
        Path(__file__).resolve().parent.parent / "data" / "env" / "ee-zc-povertymapping-0c4c39483d32.json",
        Path("c:/Users/Admin/povmapbackend/env/ee-zc-povertymapping-0c4c39483d32.json"),
    ]
    
    def _get_credentials():
        """Load service account credentials from the JSON key file."""
        scopes = [
            "https://www.googleapis.com/auth/earthengine",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        
        for sa_path in _POSSIBLE_SA_PATHS:
            if sa_path.exists():
                return service_account.Credentials.from_service_account_file(
                    str(sa_path),
                    scopes=scopes,
                )
        
        raise FileNotFoundError(
            f"Service account file not found. Searched: {[str(p) for p in _POSSIBLE_SA_PATHS]}"
        )

import geemap


# Base paths - script lives in scripts/ but data is in project root
BASE_DIR = Path(__file__).resolve().parent  # scripts/
PROJECT_ROOT = BASE_DIR.parent  # project root
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"  # assets/ at project root contains shapefiles, POIs, roads
EXPORTS_DIR = PROJECT_ROOT / "googleEarthExports"
STATUS_FILE = PROJECT_ROOT / "geospatial_prep_status.json"

# POIs and roads are in assets/
POI_ROADS_DIR = ASSETS_DIR

# Configuration
VERIFY_COLLECTIONS = False  # Set to True only if you need to verify counts
SCALE = 250  # Increase to 500 for even faster processing


def update_status(phase: str, message: str = "") -> None:
    data = {"phase": phase, "message": message}
    try:
        STATUS_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def _geospatial_prep_excepthook(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    try:
        msg = f"geospatial_prep.py failed: {exc_value}"
        print(msg)
        update_status("ERROR", msg)
    except Exception:
        pass

    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = _geospatial_prep_excepthook

update_status("STARTED", "geospatial_prep.py started")

# Authenticate / initialize Earth Engine
creds = _get_credentials()
ee.Initialize(creds, project=EE_PROJECT_ID)
update_status("EE_INITIALIZED", "Earth Engine initialized")

# Define 2024 date range
start_date = "2024-01-01"
end_date = "2024-12-31"

print(f"Collecting data for: {start_date} to {end_date}")
update_status("DATE_RANGE_SET", f"Collecting data for: {start_date} to {end_date}")

# Initialize layers dictionary for export
layers_dict = {}

# ============================================================================
# LOAD ROI (WITH PROPER DISSOLVE)
# ============================================================================
update_status("LOADING_SHAPEFILE", "Reading ROI shapefile")
gdf = gpd.read_file(str(ASSETS_DIR / "shapefile" / "zc04AdminBoundaries.shp"))

print(f"\n=== SHAPEFILE DIAGNOSTICS ===")
print(f"Number of features: {len(gdf)}")
print(f"CRS: {gdf.crs}")
print(f"Columns: {gdf.columns.tolist()}")

# Calculate total area
if gdf.crs and gdf.crs.is_geographic:
    # Convert to projected CRS for accurate area calculation
    gdf_projected = gdf.to_crs("EPSG:32651")  # UTM Zone 51N for Philippines
    total_area_m2 = gdf_projected.geometry.area.sum()
    total_area_km2 = total_area_m2 / 1_000_000
else:
    total_area_m2 = gdf.geometry.area.sum()
    total_area_km2 = total_area_m2 / 1_000_000

print(f"Total area of all features: {total_area_km2:.2f} km²")

if len(gdf) > 1:
    print(f"\n⚠️  Shapefile has {len(gdf)} separate features - dissolving into single geometry...")
    update_status("DISSOLVING_SHAPEFILE", f"Dissolving {len(gdf)} features")
    gdf = gdf.dissolve()
    gdf = gdf.reset_index(drop=True)
    print(f"✓ Dissolved into {len(gdf)} feature(s)")

update_status("CONVERTING_SHAPEFILE", "Converting shapefile to Earth Engine FeatureCollection")
roi = geemap.geopandas_to_ee(gdf)

update_status("INITIALIZING_MAP", "Initializing map")
print("OK: ROI ready")

update_status("ROI_READY", "ROI converted to Earth Engine and map initialized")

# ============================================================================
# LOAD ALL IMAGE COLLECTIONS (LAZY - NO .getInfo() calls)
# ============================================================================
print("\n=== LOADING IMAGE COLLECTIONS (LAZY) ===")
update_status("LOADING_COLLECTIONS", "Loading all image collections")

# Sentinel-2
s2 = (
    ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    .filterDate(start_date, end_date)
    .filterBounds(roi)
    .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    .select(['B2', 'B3', 'B4', 'B8', 'B11'])  # Only needed bands
)

# VIIRS Nighttime Lights
viirs = (
    ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG")
    .filterDate(start_date, end_date)
    .filterBounds(roi)
)

# MODIS Vegetation Index
modis_vi = (
    ee.ImageCollection("MODIS/061/MOD13A2")
    .filterDate(start_date, end_date)
    .filterBounds(roi)
)

# CHIRPS Precipitation
chirps = (
    ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
    .filterDate(start_date, end_date)
    .filterBounds(roi)
)

# Landsat 8/9
landsat = (
    ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
    .filterDate(start_date, end_date)
    .filterBounds(roi)
    .filter(ee.Filter.lt("CLOUD_COVER", 20))
)

print("OK: All collections loaded (lazy evaluation)")

# OPTIONAL: Batch verify collection sizes (single server call)
if VERIFY_COLLECTIONS:
    print("\nVerifying collection sizes (single batch call)...")
    sizes = ee.Dictionary({
        's2': s2.size(),
        'viirs': viirs.size(),
        'modis': modis_vi.size(),
        'chirps': chirps.size(),
        'landsat': landsat.size()
    }).getInfo()
    
    print(f"  Sentinel-2: {sizes['s2']} images")
    print(f"  VIIRS: {sizes['viirs']} images")
    print(f"  MODIS: {sizes['modis']} images")
    print(f"  CHIRPS: {sizes['chirps']} images")
    print(f"  Landsat: {sizes['landsat']} images")

update_status("COLLECTIONS_LOADED", "All image collections loaded")

# ============================================================================
# CREATE COMPOSITES
# ============================================================================
print("\n=== CREATING COMPOSITES ===")
update_status("COMPOSITES", "Creating image composites")

# Sentinel-2 composite
s2_composite = s2.median()

# Calculate NDVI
ndvi = s2_composite.normalizedDifference(["B8", "B4"]).rename("NDVI")

# Calculate NDBI
ndbi = s2_composite.normalizedDifference(["B11", "B8"]).rename("ndbi")

# Calculate GLCM texture features from NIR (B8) and Red (B4) bands
print("Calculating GLCM texture features from NIR and Red bands...")

# NIR band (B8) GLCM
nir_band = s2_composite.select('B8').multiply(255).toUint8()
nir_glcm = nir_band.glcmTexture(size=3)

NIR_glcm_contrast = nir_glcm.select('B8_contrast').rename('NIR_glcm_contrast')
NIR_glcm_dissimilarity = nir_glcm.select('B8_diss').rename('NIR_glcm_dissimilarity')
NIR_glcm_homogeneity = nir_glcm.select('B8_idm').rename('NIR_glcm_homogeneity')
NIR_glcm_energy = nir_glcm.select('B8_ent').rename('NIR_glcm_energy')
NIR_glcm_correlation = nir_glcm.select('B8_corr').rename('NIR_glcm_correlation')
NIR_glcm_asm = nir_glcm.select('B8_asm').rename('NIR_glcm_asm')

# Red band (B4) GLCM
red_band = s2_composite.select('B4').multiply(255).toUint8()
red_glcm = red_band.glcmTexture(size=3)

Red_glcm_contrast = red_glcm.select('B4_contrast').rename('Red_glcm_contrast')
Red_glcm_dissimilarity = red_glcm.select('B4_diss').rename('Red_glcm_dissimilarity')
Red_glcm_homogeneity = red_glcm.select('B4_idm').rename('Red_glcm_homogeneity')
Red_glcm_energy = red_glcm.select('B4_ent').rename('Red_glcm_energy')
Red_glcm_correlation = red_glcm.select('B4_corr').rename('Red_glcm_correlation')
Red_glcm_asm = red_glcm.select('B4_asm').rename('Red_glcm_asm')

# Calculate mean and standard deviation for NIR and Red bands
# Use a focal mean/stdDev with a small kernel to get local statistics
kernel = ee.Kernel.square(radius=1, units='pixels')  # 3x3 window

NIR_mean = nir_band.reduceNeighborhood(
    reducer=ee.Reducer.mean(),
    kernel=kernel
).rename('NIR_mean')

NIR_std = nir_band.reduceNeighborhood(
    reducer=ee.Reducer.stdDev(),
    kernel=kernel
).rename('NIR_std')

Red_mean = red_band.reduceNeighborhood(
    reducer=ee.Reducer.mean(),
    kernel=kernel
).rename('Red_mean')

Red_std = red_band.reduceNeighborhood(
    reducer=ee.Reducer.stdDev(),
    kernel=kernel
).rename('Red_std')

print("OK: GLCM texture features and band statistics calculated")

# VIIRS composite
viirs_composite = viirs.median()
ntl = viirs_composite.select("avg_rad")

# MODIS composite
modis_vi_composite = modis_vi.median()
modis_ndvi = modis_vi_composite.select("NDVI").multiply(0.0001)

# CHIRPS composite
precip_sum = chirps.sum().select("precipitation")

# Landsat composite and surface temperature
landsat_composite = landsat.median()
st = (
    landsat_composite.select("ST_B10")
    .multiply(0.00341802)
    .add(149.0)
    .subtract(273.15)
)

print("OK: All composites created")

# ============================================================================
# ADD LAYERS TO MAP
# ============================================================================
print("\n=== ADDING LAYERS TO MAP ===")
print("OK: Visualization skipped; proceeding with export")

# ============================================================================
# STATIC LAYERS (SRTM, WorldPop)
# ============================================================================
print("\n=== LOADING STATIC LAYERS ===")

# SRTM
srtm = ee.Image("USGS/SRTMGL1_003")
elevation = srtm.select("elevation").clip(roi)
slope = ee.Terrain.slope(elevation)

# WorldPop
worldpop_asset_id = "projects/ee-zc-povertymapping/assets/phl_pop_2024_CN_100m_R2025A_v1"
worldpop_image = ee.Image(worldpop_asset_id)
worldpop_clipped = worldpop_image.clip(roi)

print("OK: SRTM and WorldPop layers added")

# ============================================================================
# OSM DATA (POIs and Roads)
# ============================================================================
print("\n=== PROCESSING OSM DATA ===")
update_status("OSM_PROCESSING", "Processing POIs and roads")

pois_path = POI_ROADS_DIR / "POIs-OSM.geojson"
roads_path = POI_ROADS_DIR / "roads-OSM.geojson"

# Convert ROI to WGS84 for filtering
gdf_wgs84 = gdf.to_crs("EPSG:4326")
roi_geom = gdf_wgs84.unary_union

pois_gdf_for_counts = None

# Process POIs
try:
    pois_gdf = gpd.read_file(str(pois_path))
    if pois_gdf.crs != "EPSG:4326":
        pois_gdf = pois_gdf.to_crs("EPSG:4326")
    
    pois_filtered = pois_gdf[pois_gdf.intersects(roi_geom.buffer(0.01))]
    
    essential_cols = ["geometry"]
    if "amenity" in pois_filtered.columns:
        essential_cols.append("amenity")
    if "name" in pois_filtered.columns:
        essential_cols.append("name")
    
    pois_simple = pois_filtered[essential_cols].copy()
    pois_simple["geometry"] = pois_simple.geometry.simplify(0.0001)
    
    pois_simple.to_file(str(ASSETS_DIR / "POIs-simple.geojson"), driver="GeoJSON")
    pois_gdf_for_counts = pois_simple.copy()
    
    print(f"OK: Processed {len(pois_simple)} POIs")
except Exception as e:
    print(f"ERROR: Error processing POIs: {e}")

# Process Roads
try:
    roads_gdf = gpd.read_file(str(roads_path))
    if roads_gdf.crs != "EPSG:4326":
        roads_gdf = roads_gdf.to_crs("EPSG:4326")
    
    roads_filtered = roads_gdf[roads_gdf.intersects(roi_geom.buffer(0.01))]
    
    essential_cols = ["geometry"]
    if "highway" in roads_filtered.columns:
        essential_cols.append("highway")
    if "name" in roads_filtered.columns:
        essential_cols.append("name")
    
    roads_simple = roads_filtered[essential_cols].copy()
    roads_simple["geometry"] = roads_simple.geometry.simplify(0.0005)
    
    # Filter by highway type
    keep_highways = [
        "motorway", "trunk", "primary", "secondary", "tertiary",
        "trunk_link", "primary_link", "secondary_link",
        "residential", "service", "unclassified", "track"
    ]
    
    if "highway" in roads_simple.columns:
        roads_simple = roads_simple[roads_simple["highway"].isin(keep_highways)]
    
    roads_simple.to_file(str(ASSETS_DIR / "roads-simple.geojson"), driver="GeoJSON")
    
    print(f"OK: Processed {len(roads_simple)} roads")
except Exception as e:
    print(f"ERROR: Error processing roads: {e}")

# Convert to EE
pois_ee = None
roads_ee = None

try:
    pois_simple = gpd.read_file(str(ASSETS_DIR / "POIs-simple.geojson"))
    if len(pois_simple) > 1000:
        pois_sample = pois_simple.sample(n=1000, random_state=42)
        pois_ee = geemap.gdf_to_ee(pois_sample)
    else:
        pois_ee = geemap.gdf_to_ee(pois_simple)
    print("OK: POIs converted to EE")
except Exception as e:
    print(f"ERROR: Error converting POIs to EE: {e}")

try:
    roads_simple = gpd.read_file(str(ASSETS_DIR / "roads-simple.geojson"))
    if len(roads_simple) > 500:
        roads_sample = roads_simple.sample(n=500, random_state=42)
        roads_ee = geemap.gdf_to_ee(roads_sample)
    else:
        roads_ee = geemap.gdf_to_ee(roads_simple)
    print("OK: Roads converted to EE")
except Exception as e:
    print(f"ERROR: Error converting roads to EE: {e}")

# ============================================================================
# ACCESSIBILITY LAYERS
# ============================================================================
print("\n=== CREATING ACCESSIBILITY LAYERS ===")
update_status("ACCESSIBILITY", "Creating accessibility layers")

def make_accessibility_from_fc(fc, max_dist_m, layer_name, scale=50):
    if fc is None:
        print(f"ERROR: {layer_name}: FeatureCollection is None")
        return None
    try:
        fc = ee.FeatureCollection(fc)
        dist = fc.distance(max_dist_m)
        access = ee.Image.constant(max_dist_m).subtract(dist).clamp(0, max_dist_m)
        access = access.unmask(0).rename(layer_name)
        # Simplified: just clip to ROI without explicit reprojection
        access = access.clip(roi)
        return access
    except Exception as e:
        print(f"ERROR: Error creating {layer_name}: {e}")
        return None

poi_access = make_accessibility_from_fc(pois_ee, 5000, "poi_accessibility")
if poi_access is not None:
    print("OK: POI accessibility computed")

road_access = make_accessibility_from_fc(roads_ee, 2000, "road_accessibility")
if road_access is not None:
    print("OK: Road accessibility computed")

# ============================================================================
# CREATE GRID (using same method as CNN for consistency)
# ============================================================================
print("\n=== CREATING GRID ===")
update_status("CREATING_GRID", "Creating ~1km grid (UTM-based for accuracy)")

def create_1km_grid_ee(roi_fc, shapefile_gdf, grid_size_m=1000, utm_epsg=32651):
    """
    Create 1km grid using UTM projection (same method as CNN pipeline).
    This ensures grid cells are exactly 1km x 1km and match the CNN grid.
    """
    from shapely.geometry import box
    
    # Project to UTM for accurate 1km cells
    study_area_utm = shapefile_gdf.to_crs(epsg=utm_epsg)
    minx, miny, maxx, maxy = study_area_utm.total_bounds
    
    cols = int(np.ceil((maxx - minx) / grid_size_m))
    rows = int(np.ceil((maxy - miny) / grid_size_m))
    
    print(f"Grid dimensions: {cols} columns × {rows} rows")
    print(f"UTM bounds: ({minx:.0f}, {miny:.0f}) to ({maxx:.0f}, {maxy:.0f})")
    
    # Create grid cells
    grid_features = []
    for i in range(cols):
        for j in range(rows):
            cell = box(minx + i*grid_size_m, miny + j*grid_size_m,
                       minx + (i+1)*grid_size_m, miny + (j+1)*grid_size_m)
            if study_area_utm.intersects(cell).any():
                # Convert cell to WGS84 for GEE
                cell_gdf = gpd.GeoDataFrame(geometry=[cell], crs=f"EPSG:{utm_epsg}")
                cell_wgs84 = cell_gdf.to_crs(epsg=4326).geometry.iloc[0]
                
                # Create EE feature
                coords = list(cell_wgs84.exterior.coords)
                ee_geom = ee.Geometry.Polygon([coords])
                
                grid_features.append(ee.Feature(ee_geom, {
                    'x_idx': i,
                    'y_idx': j,
                    'grid_id': f"{i}_{j}",
                    'cell_id': f"cell_{i:04d}_{j:04d}"
                }))
    
    print(f"Created {len(grid_features)} grid cells intersecting ROI")
    return ee.FeatureCollection(grid_features)

# Create grid using UTM-based method (matches CNN grid)
grid = create_1km_grid_ee(roi, gdf_wgs84, grid_size_m=1000, utm_epsg=32651)

# Diagnostic: Check grid statistics
print("\n=== GRID DIAGNOSTICS ===")
grid_size = grid.size().getInfo()
print(f"Total grid cells after filtering: {grid_size}")
print(f"ROI area: {total_area_km2:.2f} km²")
print(f"Expected cells (~1km² each): ~{total_area_km2:.0f}")
print(f"Actual/Expected ratio: {grid_size/total_area_km2:.2f}x")

print("\nOK: Grid created (UTM-based, matches CNN grid)")
update_status("GRID_CREATED", "Grid created successfully")

# ============================================================================
# FAST EXTRACTION USING sampleRegions (MULTI-SAMPLE)
# ============================================================================
print("\n=== EXTRACTING GRID DATA (sampleRegions) ===")
update_status("FAST_EXTRACTION", "Using sampleRegions for multiple samples per grid cell")

# Stack all layers into one multi-band image
combined = ee.Image.cat([
    elevation.rename('elevation'),
    modis_ndvi.rename('modis_ndvi'),
    ndbi,
    ndvi,
    ntl.rename('nighttime_lights'),
    worldpop_clipped.rename('population'),
    precip_sum.rename('precipitation'),
    s2_composite.select('B4').rename('sentinel2_composite'),  # Using Red band as representative
    slope.rename('slope'),
    st.rename('surface_temp'),
    NIR_glcm_contrast,
    NIR_glcm_dissimilarity,
    NIR_glcm_homogeneity,
    NIR_glcm_energy,
    NIR_glcm_correlation,
    NIR_glcm_asm,
    Red_glcm_contrast,
    Red_glcm_dissimilarity,
    Red_glcm_homogeneity,
    Red_glcm_energy,
    Red_glcm_correlation,
    Red_glcm_asm,
    NIR_mean,
    NIR_std,
    Red_mean,
    Red_std,
])

# Add accessibility layers if available
if poi_access is not None:
    combined = combined.addBands(poi_access)
if road_access is not None:
    combined = combined.addBands(road_access)

# Extract multiple samples per grid cell
print(f"Extracting multiple samples per grid cell at {SCALE}m resolution...")
grid_with_data = combined.sampleRegions(
    collection=grid,
    scale=SCALE,
    geometries=True,
    tileScale=4
)

print("OK: Extraction complete (multiple samples per grid cell)")
update_status("EXTRACTION_COMPLETE", "Grid data extraction complete")

# ============================================================================
# EXPORT TO CSV (NO AGGREGATION)
# ============================================================================
print("\n=== DOWNLOADING GRID DATA (LOCAL CSV) ===")
update_status("EXPORTING", "Downloading grid data as CSV")

EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

download_url = grid_with_data.getDownloadURL('csv')

local_csv_path = EXPORTS_DIR / "zc04_grid_data_2024.csv"
print(f"Downloading CSV to {local_csv_path} ...")

tmp_path = EXPORTS_DIR / "zc04_grid_data_2024.tmp"

with urllib.request.urlopen(download_url) as response, open(tmp_path, "wb") as f:
    f.write(response.read())

os.replace(tmp_path, local_csv_path)

print(f"OK: Saved grid CSV to {local_csv_path}")

# Load for statistics
df_samples = pd.read_csv(local_csv_path)
print(f"Total samples: {len(df_samples)}")

# Count unique grid cells
grid_id_cols = ['grid_id', 'x_idx', 'y_idx']
available_grid_cols = [c for c in grid_id_cols if c in df_samples.columns]
if available_grid_cols:
    if 'grid_id' in df_samples.columns:
        unique_grids = df_samples['grid_id'].nunique()
    else:
        df_samples['_temp_grid_id'] = df_samples['x_idx'].astype(str) + '_' + df_samples['y_idx'].astype(str)
        unique_grids = df_samples['_temp_grid_id'].nunique()
    print(f"Unique grid cells: {unique_grids}")
    print(f"Average samples per grid: {len(df_samples)/unique_grids:.1f}")
else:
    print("Note: Cannot determine unique grid cells (no grid_id/x_idx/y_idx columns)")

# ============================================================================
# FILL MISSING GRID CELLS (ensure all ROI cells have data)
# ============================================================================
print("\n=== CHECKING FOR MISSING GRID CELLS ===")
update_status("FILLING_MISSING", "Ensuring all ROI grid cells have data")

# Get all grid cells from the grid FeatureCollection
try:
    all_grid_cells = grid.getInfo()['features']
    all_grid_ids = set()
    grid_cell_info = {}  # Store geometry info for missing cells
    
    for feature in all_grid_cells:
        props = feature['properties']
        grid_id = props.get('grid_id') or f"{props['x_idx']}_{props['y_idx']}"
        all_grid_ids.add(grid_id)
        grid_cell_info[grid_id] = {
            'x_idx': props['x_idx'],
            'y_idx': props['y_idx'],
            'geometry': feature['geometry']
        }
    
    # Find grid cells in data
    if 'grid_id' not in df_samples.columns:
        df_samples['grid_id'] = df_samples['x_idx'].astype(str) + '_' + df_samples['y_idx'].astype(str)
    
    existing_grid_ids = set(df_samples['grid_id'].unique())
    missing_grid_ids = all_grid_ids - existing_grid_ids
    
    print(f"Total grid cells in ROI: {len(all_grid_ids)}")
    print(f"Grid cells with data: {len(existing_grid_ids)}")
    print(f"Missing grid cells: {len(missing_grid_ids)}")
    
    if missing_grid_ids:
        print(f"Adding {len(missing_grid_ids)} missing grid cells with interpolated values...")
        
        # Get column means for numeric columns (for filling)
        numeric_cols = df_samples.select_dtypes(include=[np.number]).columns.tolist()
        # Remove ID columns from fill
        fill_cols = [c for c in numeric_cols if c not in ['x_idx', 'y_idx']]
        col_means = df_samples[fill_cols].mean()
        
        # Create rows for missing cells
        missing_rows = []
        for grid_id in missing_grid_ids:
            info = grid_cell_info[grid_id]
            row = {
                'grid_id': grid_id,
                'x_idx': info['x_idx'],
                'y_idx': info['y_idx'],
            }
            # Add geometry if available
            if info['geometry']:
                import json as _json
                row['.geo'] = _json.dumps(info['geometry'])
            # Fill numeric columns with mean values
            for col in fill_cols:
                row[col] = col_means[col]
            missing_rows.append(row)
        
        # Append missing rows
        df_missing = pd.DataFrame(missing_rows)
        df_samples = pd.concat([df_samples, df_missing], ignore_index=True)
        
        # Re-save the CSV with all grid cells
        df_samples.to_csv(local_csv_path, index=False)
        print(f"Updated CSV saved with {len(df_samples)} total samples ({df_samples['grid_id'].nunique()} unique grid cells)")
        update_status("MISSING_FILLED", f"Added {len(missing_grid_ids)} missing grid cells")
    else:
        print("All grid cells have data - no filling needed")
        
except Exception as e:
    print(f"Warning: Could not fill missing grid cells: {e}")
    import traceback
    traceback.print_exc()

# ============================================================================
# ADD POI COUNTS TO CSV
# ============================================================================
print("\n=== ADDING POI COUNTS TO CSV ===")
update_status("POI_COUNTING", "Adding POI counts by category")

if pois_gdf_for_counts is not None:
    print(f"Processing POI counts for {len(df_samples)} samples...")
    
    # Define POI categories
    poverty_relevant_poi_types = {
        "school": ["school", "kindergarten", "college", "university"],
        "healthcare": ["hospital", "clinic", "doctors", "pharmacy", "dentist"],
        "market": ["marketplace", "supermarket", "convenience", "mall"],
        "finance": ["bank", "atm"],
        "transport": ["bus_station", "ferry_terminal", "taxi"],
        "food": ["restaurant", "fast_food", "cafe"],
        "water": ["drinking_water", "water_point"],
        "worship": ["place_of_worship"],
        "public": ["community_centre", "social_facility", "townhall", 
                   "post_office", "police"],
    }
    
    # Function to count POIs in a geometry
    def count_pois_in_wkt(wkt_geom):
        from shapely import wkt
        import json as _json
        from shapely.geometry import shape as _shape
        try:
            s = wkt_geom
            if isinstance(s, str) and s.strip().startswith("{"):
                cell_geom = _shape(_json.loads(s))
            else:
                cell_geom = wkt.loads(s)
            pois_in_cell = pois_gdf_for_counts[pois_gdf_for_counts.intersects(cell_geom)]
            
            counts = {}
            for category, amenity_types in poverty_relevant_poi_types.items():
                if "amenity" in pois_in_cell.columns:
                    count = pois_in_cell[pois_in_cell["amenity"].isin(amenity_types)].shape[0]
                else:
                    count = 0
                counts[f"poi_count_{category}"] = count
            
            counts["poi_count_total"] = len(pois_in_cell)
            return pd.Series(counts)
        except Exception:
            return pd.Series({f"poi_count_{cat}": 0 
                            for cat in poverty_relevant_poi_types.keys()} | 
                           {"poi_count_total": 0})
    
    # Add POI counts to each sample
    geom_col = '.geo' if '.geo' in df_samples.columns else ('geometry' if 'geometry' in df_samples.columns else None)
    if geom_col is None:
        print("WARNING: No geometry column - skipping POI counts")
    else:
        poi_counts = df_samples[geom_col].apply(count_pois_in_wkt)
        df_samples = pd.concat([df_samples, poi_counts], axis=1)
    
    # Save final CSV
    df_samples.to_csv(local_csv_path, index=False)
    print(f"OK: Added POI counts to {local_csv_path}")
    if geom_col:
        print(f"   POI count columns: {list(poi_counts.columns)}")
else:
    print("WARNING: No POI data available for counting")

update_status("DONE", "geospatial_prep.py finished successfully")

print("\n" + "="*60)
print("ALL PROCESSING COMPLETE")
print("="*60)
if available_grid_cols:
    print(f"Total samples: {len(df_samples)}")
    print(f"Unique grid cells: {unique_grids}")
    print(f"Average samples per cell: {len(df_samples)/unique_grids:.1f}")
print(f"ROI area: {total_area_km2:.2f} km²")
print(f"Output: {local_csv_path}")
print("="*60)