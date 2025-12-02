#!/usr/bin/env python3
"""
Repredict Poverty Mapping – Zamboanga City (Single-File Pipeline)

Top-down workflow to: (1) optionally generate a GEE export script for a Sentinel-2 composite,
(2) build or reuse a 1 km grid, (3) extract CNN-ready tiles from a local GeoTIFF, (4) compute
S2 engineered features, (5) load trained fusion model + scaler, (6) predict ROI, (7) fill gaps.

Run example:
  python repredict_zamboanga.py \
    --year 2024 \
    --roi_shapefile data/shapefile/zc04AdminBoundaries_gcs.shp \
    --sentinel2_tif data/satellite_imagery/sentinel2_zamboanga_2024_improved.tif \
    --model_path output/fusion_pytorch_1km/best_fusion_model_1km_fold1.pth \
    --scaler_path output/fusion_pytorch_1km/s2_scaler_grid.pkl

Optional: write a ready-to-paste Google Earth Engine (GEE) JS export script:
  python repredict_zamboanga.py --year 2024 --write_gee_js \
    --gee_asset projects/ee-jerardregalado19/assets/zamboanga_city
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import datetime as dt
from pathlib import Path

import requests
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box, mapping

import rasterio
from rasterio.windows import Window, from_bounds

from PIL import Image, ImageEnhance
from skimage import feature, filters, color, exposure
from scipy import ndimage
from scipy.spatial import cKDTree

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
import torchvision.transforms as transforms

try:
    import ee
except Exception:
    ee = None

try:
    from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights  # newer torchvision
    _HAS_TV_WEIGHTS = True
except Exception:
    from torchvision.models import efficientnet_b0  # fallback
    _HAS_TV_WEIGHTS = False

import joblib
from tqdm import tqdm


# ============================================================================
# SECTION 1: CONFIG + UTILITIES
# ============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repredict poverty mapping – single-file pipeline")
    p.add_argument("--year", type=int, default=2024, help="Target year")
    p.add_argument("--roi_shapefile", type=str, required=False,
                   default="data/shapefile/zc04AdminBoundaries_gcs.shp",
                   help="Shapefile for Zamboanga City boundary")
    p.add_argument("--sentinel2_tif", type=str, required=False,
                   default=None,
                   help="Path to local Sentinel-2 composite GeoTIFF for the target year")
    p.add_argument("--grid_gpkg", type=str, required=False,
                   default="output/grids/grid_1km.gpkg",
                   help="Path to existing 1 km grid geopackage (will be created if missing)")
    p.add_argument("--output_dir", type=str, required=False, default=None,
                   help="Output root directory. Default: output/cnn_reuse_1km_{YEAR}")
    p.add_argument("--img_size", type=int, default=224, help="CNN tile size (pixels)")

    # Model/scaler paths
    p.add_argument("--model_path", type=str, required=True,
                   help="Trained fusion model .pth file")
    p.add_argument("--scaler_path", type=str, required=True,
                   help="StandardScaler .pkl for S2 features")

    # GEE script helper
    p.add_argument("--write_gee_js", action="store_true",
                   help="Write a simplified GEE JS export script for the given year")
    p.add_argument("--gee_asset", type=str, default="projects/ee-jerardregalado19/assets/zamboanga_city",
                   help="GEE asset path to the ROI FeatureCollection")
    p.add_argument("--ee_key", type=str, default="env/ee-zc-povertymapping-0c4c39483d32.json",
                   help="Service account JSON for Earth Engine authentication")
    p.add_argument("--force_download", action="store_true",
                   help="Re-download Sentinel-2 GeoTIFF even if already present")
    p.add_argument("--download_scale", type=int, default=10,
                   help="Export scale in meters for the Sentinel-2 GeoTIFF (default: 10m)")

    # Compute device
    p.add_argument("--cpu", action="store_true", help="Force CPU inference")

    return p.parse_args()


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def ee_init_from_service_account(key_path: Path) -> None:
    if ee is None:
        raise ImportError("earthengine-api is required to download Sentinel-2 composites. Install it via pip.")
    with open(key_path, encoding="utf-8") as f:
        creds = json.load(f)
    sa_email = creds.get("client_email")
    if not sa_email:
        raise ValueError("Service account JSON missing client_email")
    credentials = ee.ServiceAccountCredentials(sa_email, key_path.as_posix())
    ee.Initialize(credentials)


def build_roi_geometry(shapefile_path: Path):
    gdf = gpd.read_file(shapefile_path).to_crs(epsg=4326)
    geom = gdf.unary_union
    if geom.is_empty:
        raise ValueError("ROI shapefile contains no geometry.")
    return mapping(geom)


def build_ee_roi(shapefile_path: Path):
    geom_json = build_roi_geometry(shapefile_path)
    return ee.Geometry(geom_json)


def build_s2_composite(year: int, roi: ee.Geometry):
    def mask_s2(image):
        qa = image.select('QA60')
        cloud_bit_mask = 1 << 10
        cirrus_bit_mask = 1 << 11
        mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cirrus_bit_mask).eq(0))
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


def rotate_existing_geotiff(path: Path) -> None:
    if not path.exists():
        return
    try:
        with rasterio.open(path) as src:
            dt_info = src.tags().get('TIFFTAG_DATETIME') or src.tags().get('DATE_TIME')
    except Exception:
        dt_info = None
    if dt_info:
        try:
            dt_obj = dt.datetime.strptime(dt_info.split('.')[0], "%Y:%m:%d %H:%M:%S")
        except Exception:
            dt_obj = dt.datetime.fromtimestamp(path.stat().st_mtime)
    else:
        dt_obj = dt.datetime.fromtimestamp(path.stat().st_mtime)

    backup = path.with_name(f"{path.stem}_{dt_obj.strftime('%Y%m%d_%H%M%S')}{path.suffix}")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.stem}_{dt_obj.strftime('%Y%m%d_%H%M%S')}_{counter}{path.suffix}")
        counter += 1
    path.rename(backup)
    print(f"Backed up old GeoTIFF to: {backup}")


def download_from_ee(img: ee.Image, region: dict, out_path: Path, scale: int = 10) -> None:
    params = {
        'region': region,
        'scale': scale,
        'crs': 'EPSG:4326',
        'fileFormat': 'GEO_TIFF'
    }
    url = img.getDownloadURL(params)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(out_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)


def ensure_sentinel2_geo(args: argparse.Namespace, year: int) -> Path:
    if args.sentinel2_tif:
        return Path(args.sentinel2_tif)

    target = Path(f"data/satellite_imagery/sentinel2_zamboanga_{year}_improved.tif")
    need_download = args.force_download or not target.exists()
    if not need_download:
        return target

    ee_key = Path(args.ee_key)
    if not ee_key.exists():
        raise FileNotFoundError(f"Earth Engine key not found at {ee_key}")
    print("Init Earth Engine from service account…")
    ee_init_from_service_account(ee_key)
    print("Building ROI geometry…")
    roi = build_ee_roi(Path(args.roi_shapefile))
    print("Building Sentinel-2 composite (this can take a minute)…")
    comp = build_s2_composite(year, roi)
    if target.exists():
        rotate_existing_geotiff(target)
    print(f"Downloading Sentinel-2 composite (scale={args.download_scale}m) via getDownloadURL…")
    download_from_ee(comp, build_roi_geometry(Path(args.roi_shapefile)), target, scale=args.download_scale)
    print(f"Saved Sentinel-2 GeoTIFF → {target}")
    return target

# ============================================================================
# SECTION 2: OPTIONAL – WRITE SIMPLIFIED GEE JS EXPORT SCRIPT
# ============================================================================

def write_gee_export_js(out_js: Path, roi_asset: str, year: int) -> None:
    """Write a ready-to-paste GEE JavaScript to export a cloud-masked S2 composite."""
    js = f"""// Simplified Sentinel-2 Composite Export for Zamboanga (Year: {year})
// Open this in https://code.earthengine.google.com/

var roi = ee.FeatureCollection('{roi_asset}');
Map.centerObject(roi, 11);

function maskS2clouds(image) {{
  var qa = image.select('QA60');
  var cloudBitMask = 1 << 10;
  var cirrusBitMask = 1 << 11;
  var mask = qa.bitwiseAnd(cloudBitMask).eq(0)
             .and(qa.bitwiseAnd(cirrusBitMask).eq(0));
  return image.updateMask(mask).divide(10000);
}}

var start = '{year}-01-01';
var end   = '{year}-12-31';
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterDate(start, end)
  .filterBounds(roi)
  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 15))
  .map(maskS2clouds);

var p40 = s2.reduce(ee.Reducer.percentile([40])).clip(roi);
var med = s2.median().clip(roi);
var comp = p40.unmask(med);
var exportImage = comp.select(
  ['B2_p40','B3_p40','B4_p40','B8_p40','B11_p40','B12_p40'],
  ['B2','B3','B4','B8','B11','B12']
);

Export.image.toDrive({{
  image: exportImage,
  description: 'sentinel2_zamboanga_{year}_improved',
  folder: 'EarthEngineExports',
  fileNamePrefix: 'sentinel2_zamboanga_{year}_improved',
  scale: 10,
  region: roi,
  maxPixels: 1e13,
  crs: 'EPSG:4326',
  fileFormat: 'GeoTIFF',
  formatOptions: {{ cloudOptimized: true }}
}});
"""
    out_js.write_text(js, encoding="utf-8")


# ============================================================================
# SECTION 3: GRID CREATION/LOADING (1 KM)
# ============================================================================

def create_1km_grid(shapefile_path: Path, output_gpkg: Path, grid_size: int = 1000, utm_epsg: int = 32651) -> gpd.GeoDataFrame:
    """Create a 1 km × 1 km grid over the ROI and save to GPKG (WGS84)."""
    study_area = gpd.read_file(shapefile_path)
    study_area_utm = study_area.to_crs(epsg=utm_epsg)

    minx, miny, maxx, maxy = study_area_utm.total_bounds
    cols = int(np.ceil((maxx - minx) / grid_size))
    rows = int(np.ceil((maxy - miny) / grid_size))

    grid_cells, cell_ids = [], []
    for i in tqdm(range(cols), desc="Columns"):
        for j in range(rows):
            cell = box(minx + i*grid_size, miny + j*grid_size,
                       minx + (i+1)*grid_size, miny + (j+1)*grid_size)
            if study_area_utm.intersects(cell).any():
                grid_cells.append(cell)
                cell_ids.append(f"cell_{i:04d}_{j:04d}")

    grid_gdf = gpd.GeoDataFrame({"cell_id": cell_ids, "geometry": grid_cells}, crs=study_area_utm.crs)

    # Add centroid + bounds in WGS84
    grid_latlon = grid_gdf.to_crs(epsg=4326)
    grid_gdf["centroid_lon"] = grid_latlon.geometry.centroid.x
    grid_gdf["centroid_lat"] = grid_latlon.geometry.centroid.y
    b = grid_latlon.bounds
    grid_gdf["minx"], grid_gdf["miny"], grid_gdf["maxx"], grid_gdf["maxy"] = b["minx"], b["miny"], b["maxx"], b["maxy"]

    # Save in WGS84
    out = grid_gdf.to_crs(epsg=4326)
    ensure_dir(output_gpkg.parent)
    out.to_file(output_gpkg, driver="GPKG")
    return out


# ============================================================================
# SECTION 4: GEO-TIFF → CNN TILES (224×224)
# ============================================================================

def extract_images_advanced(grid_gdf: gpd.GeoDataFrame, sentinel2_path: Path, output_dir: Path, img_size: int = 224) -> pd.DataFrame:
    """Read Sentinel-2 GeoTIFF locally and export per-cell RGB tiles with adaptive fixes."""
    ensure_dir(output_dir)
    metadata, stats = [], {"standard": 0, "clahe": 0, "gamma": 0, "synthetic": 0, "existing": 0, "failed": 0}

    with rasterio.open(sentinel2_path) as src:
        if grid_gdf.crs != src.crs:
            grid_gdf = grid_gdf.to_crs(src.crs)

        n_bands = min(src.count, 6)

        for _, row in tqdm(grid_gdf.iterrows(), total=len(grid_gdf), desc="Extracting tiles"):
            cell_id = row["cell_id"]
            img_path = output_dir / f"{cell_id}.png"
            if img_path.exists():
                metadata.append({"cell_id": cell_id, "filepath": str(img_path),
                                 "centroid_lon": row["centroid_lon"], "centroid_lat": row["centroid_lat"],
                                 "processing": "existing"})
                stats["existing"] += 1
                continue

            try:
                bounds = row.geometry.bounds
                window = from_bounds(*bounds, transform=src.transform)
                r0, c0 = max(0, int(window.row_off)), max(0, int(window.col_off))
                r1 = min(src.height, int(window.row_off + window.height))
                c1 = min(src.width, int(window.col_off + window.width))
                if r1 <= r0 or c1 <= c0:
                    stats["failed"] += 1
                    continue
                data = src.read(list(range(1, n_bands+1)), window=Window(c0, r0, c1-c0, r1-r0))
                if data.size == 0 or np.all(data == 0):
                    stats["failed"] += 1
                    continue

                data = np.transpose(data, (1, 2, 0))
                data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
                mean_val = float(data.mean())

                if mean_val < 1:
                    # completely dark → synthetic
                    h, w, c = data.shape
                    synth = np.random.normal(20, 10, (h, w, min(3, c)))
                    synth = filters.gaussian(synth, sigma=2)
                    synth = np.clip(synth, 0, 50)
                    rgb = synth[:, :, :3] if c >= 3 else np.stack([synth[:, :, 0]]*3, axis=-1)
                    method = "synthetic"; stats["synthetic"] += 1
                else:
                    rgb = data[:, :, :3] if data.shape[2] >= 3 else np.stack([data[:, :, 0]]*3, axis=-1)
                    # percentile normalization
                    if np.any(rgb > 0):
                        if mean_val < 10:
                            pl, ph = np.percentile(rgb[rgb>0], 0.5), np.percentile(rgb[rgb>0], 99.5)
                        elif mean_val < 50:
                            pl, ph = np.percentile(rgb[rgb>0], 1), np.percentile(rgb[rgb>0], 99)
                        else:
                            pl, ph = np.percentile(rgb[rgb>0], 2), np.percentile(rgb[rgb>0], 98)
                    else:
                        pl, ph = 0, 1
                    if ph - pl < (0.01 if mean_val < 10 else 0.1):
                        ph = pl + (0.01 if mean_val < 10 else 0.1)
                    rgb = np.clip((rgb - pl)/(ph - pl), 0, 1)

                    if mean_val < 10:
                        # CLAHE + gamma
                        rgbc = np.zeros_like(rgb)
                        for i in range(3):
                            ch = (rgb[:, :, i]*255).astype(np.uint8)
                            ch = exposure.equalize_adapthist(ch, clip_limit=0.03)
                            rgbc[:, :, i] = ch
                        rgb = np.power(rgbc, 0.4)
                        method = "clahe"; stats["clahe"] += 1
                    elif mean_val < 50:
                        rgb = np.power(rgb, 0.6)
                        method = "gamma"; stats["gamma"] += 1
                    else:
                        method = "standard"; stats["standard"] += 1

                rgb_u8 = (rgb*255).astype(np.uint8)
                if rgb_u8.max() < 10:
                    rgb_u8 = np.clip(rgb_u8 + 10, 0, 255).astype(np.uint8)
                img = Image.fromarray(rgb_u8, mode="RGB").resize((img_size, img_size), Image.Resampling.LANCZOS)
                img = ImageEnhance.Sharpness(img).enhance(1.2)
                img.save(img_path, optimize=True)

                metadata.append({"cell_id": cell_id, "filepath": str(img_path),
                                 "centroid_lon": row["centroid_lon"], "centroid_lat": row["centroid_lat"],
                                 "processing": method})
            except Exception:
                stats["failed"] += 1
                continue

    return pd.DataFrame(metadata)


# ============================================================================
# SECTION 5: S2 ENGINEERED FEATURES (GRID LEVEL)
# ============================================================================

def compute_grid_s2_features(grid_gdf: gpd.GeoDataFrame, sentinel2_path: Path, output_csv: Path) -> pd.DataFrame:
    """Compute band stats + texture/color features (aligned with training)."""
    ensure_dir(output_csv.parent)
    rows: list[dict] = []

    with rasterio.open(sentinel2_path) as src:
        if grid_gdf.crs != src.crs:
            grid_gdf = grid_gdf.to_crs(src.crs)

        for _, row in tqdm(grid_gdf.iterrows(), total=len(grid_gdf), desc="S2 features"):
            cell_id = row["cell_id"]
            try:
                window = from_bounds(*row.geometry.bounds, transform=src.transform)
                window = window.intersection(Window(0, 0, src.width, src.height))
                if window.width <= 0 or window.height <= 0:
                    continue
                data = src.read(window=window)
                if data.size == 0 or np.all(data == 0):
                    continue
                data = np.transpose(data, (1, 2, 0))
                data = np.nan_to_num(data, nan=0.0)

                feats = {"cell_id": cell_id}
                n_b = min(10, data.shape[2])
                for bidx in range(n_b):
                    band = data[:, :, bidx]
                    pre = f"b{bidx}"
                    feats[f"{pre}_mean"] = float(np.mean(band))
                    feats[f"{pre}_std"]  = float(np.std(band))
                    feats[f"{pre}_min"]  = float(np.min(band))
                    feats[f"{pre}_max"]  = float(np.max(band))
                    feats[f"{pre}_p25"]  = float(np.percentile(band, 25))
                    feats[f"{pre}_p50"]  = float(np.percentile(band, 50))
                    feats[f"{pre}_p75"]  = float(np.percentile(band, 75))

                if data.shape[2] >= 3:
                    rgb = data[:, :, :3]
                    rgb_n = (rgb - rgb.min())/(rgb.max() - rgb.min() + 1e-8)
                    if rgb_n.max() > 0:
                        gray = color.rgb2gray(rgb_n)
                        try:
                            hog = feature.hog(gray, pixels_per_cell=(8, 8), cells_per_block=(2, 2), visualize=False)
                            # keep up to 20 dims for reuse compatibility
                            if len(hog) > 20:
                                hog = hog[:20]
                            for i, val in enumerate(hog):
                                feats[f"hog_{i:02d}"] = float(val)
                        except Exception:
                            pass
                        try:
                            gx = filters.sobel_v(gray); gy = filters.sobel_h(gray)
                            gm = np.sqrt(gx**2 + gy**2)
                            feats["gradient_mag_mean"] = float(np.mean(gm))
                            feats["gradient_mag_std"]  = float(np.std(gm))
                            feats["gradient_dir_mean"] = float(np.mean(np.arctan2(gy, gx)))
                            feats["gradient_dir_std"]  = float(np.std(np.arctan2(gy, gx)))
                        except Exception:
                            pass
                        try:
                            hsv = color.rgb2hsv(rgb_n)
                            feats["hsv_h_mean"] = float(np.mean(hsv[:, :, 0]))
                            feats["hsv_h_std"]  = float(np.std(hsv[:, :, 0]))
                            feats["hsv_s_mean"] = float(np.mean(hsv[:, :, 1]))
                            feats["hsv_s_std"]  = float(np.std(hsv[:, :, 1]))
                            feats["hsv_v_mean"] = float(np.mean(hsv[:, :, 2]))
                            feats["hsv_v_std"]  = float(np.std(hsv[:, :, 2]))
                        except Exception:
                            pass

                rows.append(feats)
            except Exception:
                continue

    df = pd.DataFrame(rows).fillna(0)
    df.to_csv(output_csv, index=False)
    return df


# ============================================================================
# SECTION 6: MODEL + SCALER
# ============================================================================

class DualStreamCNN(nn.Module):
    """EfficientNet-B0 backbone + MLP for S2 features, fused for regression."""
    def __init__(self, s2_dim: int, dropout: float = 0.2):
        super().__init__()
        if _HAS_TV_WEIGHTS:
            self.backbone = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1)
        else:
            # fallback for older torchvision
            try:
                self.backbone = efficientnet_b0(pretrained=True)
            except Exception:
                self.backbone = efficientnet_b0()
        img_features = self.backbone.classifier[1].in_features
        self.backbone.classifier = nn.Identity()

        self.s2_mlp = nn.Sequential(
            nn.Linear(s2_dim, 256), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(256, 128), nn.ReLU(), nn.Dropout(dropout)
        )
        self.fusion = nn.Sequential(
            nn.Linear(img_features + 128, 256), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(256, 64), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(64, 1)
        )

    def forward(self, image, s2):
        img_features = self.backbone(image)
        s2_features  = self.s2_mlp(s2)
        x = torch.cat([img_features, s2_features], dim=1)
        out = self.fusion(x)
        return out, x


def load_scaler(path: Path):
    return joblib.load(path)


def load_fusion_model(model_path: Path, s2_dim: int, device: torch.device, dropout: float = 0.2) -> DualStreamCNN:
    model = DualStreamCNN(s2_dim=s2_dim, dropout=dropout)
    ckpt = torch.load(model_path, map_location=device)
    state = ckpt.get("model_state_dict", ckpt)
    model.load_state_dict(state, strict=True)
    model.to(device)
    model.eval()
    return model


# ============================================================================
# SECTION 7: PREDICTION + GAP FILLING
# ============================================================================

def align_features_to_scaler(s2_df: pd.DataFrame, scaler) -> tuple[pd.DataFrame, list[str]]:
    cols = [c for c in s2_df.columns if c != "cell_id"]
    if hasattr(scaler, "feature_names_in_"):
        needed = list(scaler.feature_names_in_)
        # add missing columns with zeros
        for c in needed:
            if c not in s2_df.columns:
                s2_df[c] = 0.0
        return s2_df[needed].copy(), needed
    return s2_df[cols].copy(), cols


def predict_entire_roi(model: DualStreamCNN, scaler, grid_gdf: gpd.GeoDataFrame,
                       grid_images_df: pd.DataFrame, grid_s2_features_df: pd.DataFrame,
                       device: torch.device, batch_size: int = 16) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    data = grid_images_df.merge(grid_s2_features_df, on="cell_id", how="inner")
    if len(data) == 0:
        raise ValueError("No valid cells with both images and S2 features.")

    s2_scaled, feature_cols = align_features_to_scaler(data, scaler)
    s2_scaled = scaler.transform(s2_scaled.values.astype(np.float32))

    filepaths = data["filepath"].values
    cell_ids  = data["cell_id"].values

    tform = transforms.Compose([
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])

    preds, used_ids = [], []
    with torch.no_grad():
        for i in tqdm(range(0, len(filepaths), batch_size), desc="Predicting"):
            j = min(i + batch_size, len(filepaths))
            paths = filepaths[i:j]
            s2b = s2_scaled[i:j]

            imgs, keep = [], []
            for k, pth in enumerate(paths):
                try:
                    img = Image.open(pth).convert("RGB")
                    imgs.append(tform(img))
                    keep.append(k)
                except Exception:
                    continue
            if not imgs:
                continue

            x = torch.stack(imgs).to(device)
            s = torch.tensor(s2b[keep], dtype=torch.float32).to(device)
            y, _ = model(x, s)
            preds.extend(y.cpu().numpy().reshape(-1))
            used_ids.extend(cell_ids[i + np.array(keep)])

    out_df = pd.DataFrame({"cell_id": used_ids, "predicted_poverty": preds})
    grid_complete = grid_gdf.merge(out_df, on="cell_id", how="left")
    return grid_complete, out_df


def fill_gaps_with_interpolation(grid_complete: gpd.GeoDataFrame, k_neighbors: int = 5) -> gpd.GeoDataFrame:
    miss = grid_complete["predicted_poverty"].isna()
    if not miss.any():
        grid_complete["is_interpolated"] = False
        return grid_complete

    valid = grid_complete[~miss].copy()
    missing = grid_complete[miss].copy()

    vxy = np.column_stack([valid.geometry.centroid.x, valid.geometry.centroid.y])
    vy  = valid["predicted_poverty"].values
    mxy = np.column_stack([missing.geometry.centroid.x, missing.geometry.centroid.y])

    tree = cKDTree(vxy)
    k = min(k_neighbors, len(vxy))
    dists, idxs = tree.query(mxy, k=k)

    interp = []
    for i in range(len(mxy)):
        if k == 1:
            di = np.array([dists[i]]); ii = np.array([idxs[i]])
        else:
            di = dists[i]; ii = idxs[i]
        w = 1.0/(di + 1e-6); w = w/w.sum()
        interp.append(float(np.sum(vy[ii] * w)))

    grid_complete = grid_complete.copy()
    grid_complete.loc[miss, "predicted_poverty"] = interp
    grid_complete["is_interpolated"] = False
    grid_complete.loc[miss, "is_interpolated"] = True
    return grid_complete


# ============================================================================
# SECTION 8: MAIN PIPELINE (TOP-DOWN)
# ============================================================================

def main():
    args = parse_args()

    year = args.year
    base = Path.cwd()

    # Resolve default output dir
    out_root = Path(args.output_dir) if args.output_dir else Path(f"output/cnn_reuse_1km_{year}")
    img_dir = out_root / "images"
    ensure_dir(out_root); ensure_dir(img_dir)

    # Optionally write the GEE JS export script
    if args.write_gee_js:
        gee_dir = ensure_dir(out_root / "gee")
        out_js = gee_dir / f"s2_export_{year}.js"
        write_gee_export_js(out_js, args.gee_asset, year)
        print(f"[GEE] Wrote export script → {out_js}")
        print("Open https://code.earthengine.google.com/ and paste/run the script.")

    # Load or create grid
    grid_gpkg = Path(args.grid_gpkg)
    if grid_gpkg.exists():
        print(f"Loading grid from {grid_gpkg}")
        grid = gpd.read_file(grid_gpkg)
    else:
        print("No grid found; creating 1 km grid…")
        grid = create_1km_grid(Path(args.roi_shapefile), grid_gpkg, grid_size=1000, utm_epsg=32651)
        print(f"Created {len(grid)} cells → {grid_gpkg}")

    print("Resolving Sentinel-2 GeoTIFF…")
    s2_tif = ensure_sentinel2_geo(args, year)
    print(f"Sentinel-2 GeoTIFF: {s2_tif} | Exists: {s2_tif.exists()}")
    if not s2_tif.exists():
        print("ERROR: Unable to obtain Sentinel-2 GeoTIFF even after download.")
        sys.exit(1)

    # Extract CNN-ready tiles (PNG)
    print("Extracting Sentinel-2 tiles…")
    grid_images = extract_images_advanced(grid, s2_tif, img_dir, img_size=args.img_size)
    grid_images_csv = out_root / f"grid_images_{year}.csv"
    grid_images.to_csv(grid_images_csv, index=False)
    print(f"Saved grid image index → {grid_images_csv} | {len(grid_images)} items")

    # Compute S2 engineered features
    print("Computing S2 engineered features…")
    s2_feat_csv = out_root / f"grid_s2_features_{year}.csv"
    s2_df = compute_grid_s2_features(grid, s2_tif, s2_feat_csv)
    print(f"Saved S2 features → {s2_feat_csv} | {len(s2_df)} rows × {len([c for c in s2_df.columns if c!='cell_id'])} feats")

    # Load scaler + model
    print("Loading scaler and model…")
    scaler = load_scaler(Path(args.scaler_path))
    device = torch.device("cpu" if (args.cpu or not torch.cuda.is_available()) else "cuda")

    # Determine S2 dim aligned to scaler
    _tmp_aligned, feature_cols = align_features_to_scaler(s2_df.copy(), scaler)
    s2_dim = len(feature_cols)

    model = load_fusion_model(Path(args.model_path), s2_dim=s2_dim, device=device, dropout=0.2)
    print(f"Model loaded on {device} | S2 dim: {s2_dim}")

    # Predict entire ROI
    print("Running predictions…")
    grid_complete, results_df = predict_entire_roi(model, scaler, grid, grid_images, s2_df, device=device, batch_size=16)

    # Save raw predictions
    raw_csv = out_root / f"grid_predictions_{year}.csv"
    raw_gpkg = out_root / f"grid_predictions_{year}.gpkg"
    results_df.to_csv(raw_csv, index=False)
    grid_complete.to_file(raw_gpkg, driver="GPKG")
    print(f"Saved raw predictions → {raw_csv} and {raw_gpkg}")

    # Gap filling (IDW)
    print("Filling gaps with IDW…")
    grid_filled = fill_gaps_with_interpolation(grid_complete, k_neighbors=5)

    filled_csv = out_root / f"grid_predictions_{year}_filled.csv"
    filled_gpkg = out_root / f"grid_predictions_{year}_filled.gpkg"
    grid_filled.drop(columns="geometry").to_csv(filled_csv, index=False)
    grid_filled.to_file(filled_gpkg, driver="GPKG")
    print(f"Saved filled predictions → {filled_csv} and {filled_gpkg}")

    print("Done.")


if __name__ == "__main__":
    main()
