from pathlib import Path
import json

import pandas as pd
import geopandas as gpd
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

GRID_GEOJSON_PATH = Path(r"c:/Users/Jerard/poverty_mapping/assets/grid_with_comprehensive_data.geojson")
MERGED_PREDICTIONS_PATH = Path(r"c:/Users/Jerard/poverty_mapping/output/grid_predictions_comparison.csv")
GRID_GPKG_PATH = Path(r"c:/Users/Jerard/Documents/GitHub/CNN Mapping/output/grids/grid_1km_all.gpkg")
CNN_PRED_PATH = Path(r"c:/Users/Jerard/Documents/GitHub/CNN Mapping/output/fusion_pytorch_1km/all_cells_predictions_1km.csv")
SHAPEFILE_PATH = Path(r"c:/Users/Jerard/Documents/GitHub/CNN Mapping/data/shapefile/zc04AdminBoundaries_gcs.shp")

app = FastAPI(title="Zamboanga Poverty Mapping")

static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _detect_barangay_column(gdf: gpd.GeoDataFrame) -> str | None:
    # First, look for columns that clearly refer to barangay and are not ID/code fields
    brgy_candidates: list[str] = []
    for col in gdf.columns:
        lower = str(col).lower()
        if "brgy" in lower or "barangay" in lower or "bgy" in lower:
            brgy_candidates.append(col)

    if brgy_candidates:
        # Prefer columns that look like names (avoid ones containing id/code/psgc)
        for col in brgy_candidates:
            lower = str(col).lower()
            if not ("code" in lower or "psgc" in lower or "id" in lower):
                return col
        # Fall back to the first barangay-related column if all look like codes
        return brgy_candidates[0]

    # Second, try any generic name-like column (often used for admin names)
    name_candidates: list[str] = []
    for col in gdf.columns:
        lower = str(col).lower()
        if "name" in lower or "nm" in lower:
            name_candidates.append(col)
    if name_candidates:
        return name_candidates[0]

    # Finally, fall back to the first text column (original behavior)
    text_cols: list[str] = []
    for col in gdf.columns:
        if gdf[col].dtype == object:
            text_cols.append(col)
    return text_cols[0] if text_cols else None


def _to_geojson_dict(gdf: gpd.GeoDataFrame) -> dict:
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    geojson_str = gdf.to_json(drop_id=True)
    return json.loads(geojson_str)


def _prepare_data() -> dict:
    roi_gdf = gpd.read_file(SHAPEFILE_PATH)

    # Prefer the known barangay name column if present, otherwise fall back
    if "adm4_en" in roi_gdf.columns:
        barangay_col = "adm4_en"
    else:
        barangay_col = _detect_barangay_column(roi_gdf)

    grid_gdf = gpd.read_file(GRID_GEOJSON_PATH)
    merged = pd.read_csv(MERGED_PREDICTIONS_PATH)
    gdf = grid_gdf.merge(merged, on="grid_id", how="inner")

    if roi_gdf.crs and gdf.crs and roi_gdf.crs != gdf.crs:
        gdf = gdf.to_crs(roi_gdf.crs)

    if barangay_col:
        roi_subset = roi_gdf[[barangay_col, "geometry"]].copy()
        gdf = gpd.sjoin(gdf, roi_subset, how="left", predicate="intersects")
        gdf = gdf.rename(columns={barangay_col: "barangay"})
        if "index_right" in gdf.columns:
            gdf = gdf.drop(columns=["index_right"])
    else:
        gdf["barangay"] = None

    valid_cat = gdf["pred_scaled_catboost"].notna()
    if valid_cat.any():
        gdf.loc[valid_cat, "poverty_quartile_catboost"] = pd.qcut(
            gdf.loc[valid_cat, "pred_scaled_catboost"],
            q=4,
            labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
        )

    valid_rf = gdf["pred_scaled_rf"].notna()
    if valid_rf.any():
        gdf.loc[valid_rf, "poverty_quartile_rf"] = pd.qcut(
            gdf.loc[valid_rf, "pred_scaled_rf"],
            q=4,
            labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
        )

    cat_cols = [
        "grid_id",
        "geometry",
        "barangay",
        "pred_scaled_catboost",
        "poverty_quartile_catboost",
    ]
    rf_cols = [
        "grid_id",
        "geometry",
        "barangay",
        "pred_scaled_rf",
        "poverty_quartile_rf",
    ]

    cat_cols = [c for c in cat_cols if c in gdf.columns]
    rf_cols = [c for c in rf_cols if c in gdf.columns]

    cat_gdf = gdf[cat_cols].copy()
    rf_gdf = gdf[rf_cols].copy()

    if "pred_scaled_catboost" in cat_gdf.columns:
        cat_gdf["poverty_value"] = cat_gdf["pred_scaled_catboost"]
        cat_gdf["poverty_pct"] = cat_gdf["poverty_value"] * 100
    if "pred_scaled_rf" in rf_gdf.columns:
        rf_gdf["poverty_value"] = rf_gdf["pred_scaled_rf"]
        rf_gdf["poverty_pct"] = rf_gdf["poverty_value"] * 100

    cnn_pred = pd.read_csv(CNN_PRED_PATH)
    grid_gdf_cnn = gpd.read_file(GRID_GPKG_PATH)

    if roi_gdf.crs and grid_gdf_cnn.crs and roi_gdf.crs != grid_gdf_cnn.crs:
        grid_gdf_cnn = grid_gdf_cnn.to_crs(roi_gdf.crs)

    roi_union = roi_gdf.geometry.unary_union
    grid_gdf_cnn = grid_gdf_cnn[grid_gdf_cnn.geometry.intersects(roi_union)]

    cnn_map = grid_gdf_cnn.merge(cnn_pred, on="cell_id", how="left")

    valid_cnn = cnn_map["predicted_poverty"].notna()
    if valid_cnn.any():
        cnn_map.loc[valid_cnn, "poverty_quartile_cnn"] = pd.qcut(
            cnn_map.loc[valid_cnn, "predicted_poverty"],
            q=4,
            labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
        )

    if barangay_col:
        roi_subset = roi_gdf[[barangay_col, "geometry"]].copy()
        cnn_map = gpd.sjoin(cnn_map, roi_subset, how="left", predicate="intersects")
        cnn_map = cnn_map.rename(columns={barangay_col: "barangay"})
        if "index_right" in cnn_map.columns:
            cnn_map = cnn_map.drop(columns=["index_right"])
    else:
        cnn_map["barangay"] = None

    keep_cols_cnn = [
        "cell_id",
        "geometry",
        "barangay",
        "predicted_poverty",
        "poverty_quartile_cnn",
    ]
    keep_cols_cnn = [c for c in keep_cols_cnn if c in cnn_map.columns]
    cnn_gdf = cnn_map[keep_cols_cnn].copy()
    if "predicted_poverty" in cnn_gdf.columns:
        cnn_gdf["poverty_value"] = cnn_gdf["predicted_poverty"]
        cnn_gdf["poverty_pct"] = cnn_gdf["poverty_value"] * 100

    boundary_geojson = _to_geojson_dict(roi_gdf)

    # Prepare barangay label points (centroids) for map overlays
    if barangay_col and barangay_col in roi_gdf.columns:
        brgy_labels_gdf = roi_gdf[[barangay_col, "geometry"]].copy()
        brgy_labels_gdf = brgy_labels_gdf.rename(columns={barangay_col: "barangay"})
    else:
        brgy_labels_gdf = roi_gdf[["geometry"]].copy()
        brgy_labels_gdf["barangay"] = None

    brgy_labels_gdf["geometry"] = brgy_labels_gdf.geometry.centroid
    barangay_labels_geojson = _to_geojson_dict(brgy_labels_gdf[["barangay", "geometry"]])

    cat_geojson = _to_geojson_dict(cat_gdf)
    rf_geojson = _to_geojson_dict(rf_gdf)
    cnn_geojson = _to_geojson_dict(cnn_gdf)

    return {
        "boundary": boundary_geojson,
        "barangayLabels": barangay_labels_geojson,
        "models": {
            "catboost": cat_geojson,
            "rf": rf_geojson,
            "cnn": cnn_geojson,
        },
    }


_data_cache: dict | None = None


def get_data() -> dict:
    global _data_cache
    if _data_cache is None:
        _data_cache = _prepare_data()
    return _data_cache


@app.get("/")
async def root() -> FileResponse:
    index_path = static_dir / "index.html"
    return FileResponse(str(index_path))


@app.get("/api/predictions")
async def get_predictions() -> JSONResponse:
    data = get_data()
    return JSONResponse(content=data)
