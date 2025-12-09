#!/usr/bin/env python3
"""Download Sentinel-2 composite in tiles and merge."""

import sys
# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import os
import json
import base64
import zipfile
from pathlib import Path

import ee
import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import mapping
import rasterio
from rasterio.merge import merge
from google.oauth2 import service_account


def init_ee():
    """Initialize Earth Engine from environment variables."""
    b64 = os.environ.get('GEE_PRIVATE_KEY_B64')
    js = os.environ.get('GEE_PRIVATE_KEY_JSON') or os.environ.get('GEE_SERVICE_ACCOUNT_JSON')
    
    if b64:
        info = json.loads(base64.b64decode(b64))
    elif js:
        info = json.loads(js)
    else:
        raise RuntimeError("Missing GEE_PRIVATE_KEY_B64 or GEE_PRIVATE_KEY_JSON")
    
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/earthengine']
    )
    proj = os.getenv('GEE_PROJECT_ID') or os.getenv('EE_PROJECT_ID') or info.get('project_id')
    ee.Initialize(creds, project=proj)
    print('EE initialized')


def build_s2_composite(year: int, roi: ee.Geometry) -> ee.Image:
    """Build cloud-masked Sentinel-2 composite."""
    def mask_s2(image):
        qa = image.select('QA60')
        mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
        return image.updateMask(mask).divide(10000)

    s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
          .filterDate(f'{year}-01-01', f'{year}-12-31')
          .filterBounds(roi)
          .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 15))
          .map(mask_s2))

    p40 = s2.reduce(ee.Reducer.percentile([40])).clip(roi)
    med = s2.median().clip(roi)
    comp = p40.unmask(med)
    return comp.select(['B2_p40','B3_p40','B4_p40','B8_p40','B11_p40','B12_p40'],
                       ['B2','B3','B4','B8','B11','B12'])


def download_tiles(comp: ee.Image, roi_geojson: dict, out_path: Path, 
                   scale: int = 10, max_tile_size: float = 0.12):
    """Download image in tiles and merge."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Get bounding box
    if roi_geojson['type'] == 'Polygon':
        coords = roi_geojson['coordinates'][0]
    elif roi_geojson['type'] == 'MultiPolygon':
        all_coords = []
        for poly in roi_geojson['coordinates']:
            all_coords.extend(poly[0])
        coords = all_coords
    else:
        coords = roi_geojson.get('coordinates', [[]])[0]
    
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    
    width = maxx - minx
    height = maxy - miny
    n_cols = max(1, int(np.ceil(width / max_tile_size)))
    n_rows = max(1, int(np.ceil(height / max_tile_size)))
    tile_w = width / n_cols
    tile_h = height / n_rows
    
    print(f'Downloading in {n_cols}x{n_rows} = {n_cols*n_rows} tiles...')
    
    tile_dir = out_path.parent / '_tiles_tmp'
    tile_dir.mkdir(parents=True, exist_ok=True)
    tile_paths = []
    
    for row in range(n_rows):
        for col in range(n_cols):
            tile_minx = minx + col * tile_w
            tile_maxx = minx + (col + 1) * tile_w
            tile_miny = miny + row * tile_h
            tile_maxy = miny + (row + 1) * tile_h
            
            tile_region = {
                'type': 'Polygon',
                'coordinates': [[
                    [tile_minx, tile_miny],
                    [tile_maxx, tile_miny],
                    [tile_maxx, tile_maxy],
                    [tile_minx, tile_maxy],
                    [tile_minx, tile_miny]
                ]]
            }
            
            tile_path = tile_dir / f'tile_{row:02d}_{col:02d}.tif'
            
            # Check if valid GeoTIFF already exists
            if tile_path.exists() and tile_path.stat().st_size > 1000:
                try:
                    with rasterio.open(tile_path) as ds:
                        _ = ds.meta  # Quick validation
                    tile_paths.append(tile_path)
                    print(f'  Tile {row},{col} exists ({tile_path.stat().st_size/1024:.1f} KB)')
                    continue
                except Exception:
                    # Invalid file, re-download
                    tile_path.unlink(missing_ok=True)
            
            params = {
                'region': tile_region,
                'scale': scale,
                'crs': 'EPSG:4326',
                'fileFormat': 'GEO_TIFF'
            }
            
            try:
                url = comp.getDownloadURL(params)
                zip_path = tile_dir / f'tile_{row:02d}_{col:02d}.zip'
                with requests.get(url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(zip_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk:
                                f.write(chunk)
                
                # Extract all band TIFFs from the zip and stack them
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    tif_names = sorted([n for n in zf.namelist() if n.endswith('.tif')])
                    if not tif_names:
                        raise ValueError("No .tif file in zip")
                    
                    # Extract each band to temp files
                    band_data = []
                    ref_meta = None
                    for tif_name in tif_names:
                        band_tmp = tile_dir / f'_tmp_{tif_name}'
                        with zf.open(tif_name) as src, open(band_tmp, 'wb') as dst:
                            dst.write(src.read())
                        with rasterio.open(band_tmp) as ds:
                            band_data.append(ds.read(1))
                            if ref_meta is None:
                                ref_meta = ds.meta.copy()
                        band_tmp.unlink()
                    
                    # Stack bands and write multi-band GeoTIFF
                    stacked = np.stack(band_data, axis=0)
                    ref_meta.update({
                        'count': len(band_data),
                        'driver': 'GTiff',
                        'compress': 'lzw'
                    })
                    with rasterio.open(tile_path, 'w', **ref_meta) as dst:
                        dst.write(stacked)
                
                zip_path.unlink()  # Remove the zip
                tile_paths.append(tile_path)
                print(f'  Downloaded tile {row},{col} ({tile_path.stat().st_size/1024:.1f} KB, {len(band_data)} bands)')
            except Exception as e:
                print(f'  Warning: Failed tile {row},{col}: {e}')
                continue
    
    if not tile_paths:
        raise RuntimeError("No tiles downloaded successfully.")
    
    # Merge tiles
    print(f'Merging {len(tile_paths)} tiles...')
    datasets = [rasterio.open(p) for p in tile_paths]
    mosaic, out_transform = merge(datasets)
    
    out_meta = datasets[0].meta.copy()
    out_meta.update({
        "driver": "GTiff",
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "transform": out_transform,
        "compress": "lzw"
    })
    
    for ds in datasets:
        ds.close()
    
    with rasterio.open(out_path, "w", **out_meta) as dest:
        dest.write(mosaic)
    
    print(f'Merged GeoTIFF: {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)')
    
    # Cleanup
    import shutil
    shutil.rmtree(tile_dir, ignore_errors=True)
    return out_path


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, default=2024)
    parser.add_argument('--shapefile', type=str, default='assets/shapefile/zc04AdminBoundaries_gcs.shp')
    parser.add_argument('--output', type=str, default=None)
    parser.add_argument('--scale', type=int, default=10)
    parser.add_argument('--tile-size', type=float, default=0.12, help='Max tile size in degrees')
    args = parser.parse_args()
    
    init_ee()
    
    # Load ROI
    gdf = gpd.read_file(args.shapefile).to_crs(epsg=4326)
    geom = gdf.geometry.union_all()
    roi_geojson = mapping(geom)
    roi = ee.Geometry(roi_geojson)
    print(f'ROI bounds: {geom.bounds}')
    
    # Build composite
    print(f'Building S2 composite for {args.year}...')
    comp = build_s2_composite(args.year, roi)
    
    # Download
    out_path = Path(args.output) if args.output else Path(f'data/satellite_imagery/sentinel2_zamboanga_{args.year}_improved.tif')
    download_tiles(comp, roi_geojson, out_path, scale=args.scale, max_tile_size=args.tile_size)
    
    print('Done!')


if __name__ == '__main__':
    main()
