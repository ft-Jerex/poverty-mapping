from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import geopandas as gpd
from flask import Flask, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
SHAPEFILE_PATH = DATA_DIR / "shapefile" / "zc04AdminBoundaries_gcs.shp"

GRID_GEOJSON_PATH = DATA_DIR / "grid_with_comprehensive_data.geojson"
MERGED_PREDICTIONS_PATH = DATA_DIR / "grid_predictions_comparison.csv"
GRID_GPKG_PATH = DATA_DIR / "grid_1km_all.gpkg"
CNN_PRED_PATH = DATA_DIR / "all_cells_predictions_1km.csv"

CSV_DIR = ROOT / "csv_outputs"
POOR_HOUSEHOLDS_PATH = CSV_DIR / "Number of Poor Households.clean.csv"
POOR_INDIVIDUALS_PATH = CSV_DIR / "Number of Poor Individuals.clean.csv"
POOR_CHILDREN_PATH = CSV_DIR / "Poor Children Attending and Not.clean.csv"
WATER_SOURCE_PATH = CSV_DIR / "Water Source.clean.csv"
EDU_ATTAIN_PATH = CSV_DIR / "Educational Attainment.clean.csv"
POOR_EMPLOYED_PATH = CSV_DIR / "Total Number of Poor Employed _.clean.csv"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


@app.route("/")
def index() -> object:  # pragma: no cover
    return send_from_directory(app.static_folder, "index.html")


def _detect_barangay_column(gdf: gpd.GeoDataFrame) -> str | None:
    brgy_candidates: list[str] = []
    for col in gdf.columns:
        lower = str(col).lower()
        if "brgy" in lower or "barangay" in lower or "bgy" in lower:
            brgy_candidates.append(col)

    if brgy_candidates:
        for col in brgy_candidates:
            lower = str(col).lower()
            if not ("code" in lower or "psgc" in lower or "id" in lower):
                return col
        return brgy_candidates[0]

    name_candidates: list[str] = []
    for col in gdf.columns:
        lower = str(col).lower()
        if "name" in lower or "nm" in lower:
            name_candidates.append(col)
    if name_candidates:
        return name_candidates[0]

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

    quartile_ranges: dict[str, list[dict]] = {}

    valid_cat = gdf["pred_scaled_catboost"].notna()
    if valid_cat.any():
        cat_quartiles, cat_bins = pd.qcut(
            gdf.loc[valid_cat, "pred_scaled_catboost"],
            q=4,
            labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
            retbins=True,
            duplicates="drop",
        )
        gdf.loc[valid_cat, "poverty_quartile_catboost"] = cat_quartiles
        quartile_ranges["catboost"] = [
            {"label": l, "min": float(cat_bins[i]), "max": float(cat_bins[i + 1])}
            for i, l in enumerate(["Not poor", "Lower-middle", "Upper-middle", "Poorest"])
        ]

    valid_rf = gdf["pred_scaled_rf"].notna()
    if valid_rf.any():
        rf_quartiles, rf_bins = pd.qcut(
            gdf.loc[valid_rf, "pred_scaled_rf"],
            q=4,
            labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
            retbins=True,
            duplicates="drop",
        )
        gdf.loc[valid_rf, "poverty_quartile_rf"] = rf_quartiles
        quartile_ranges["rf"] = [
            {"label": l, "min": float(rf_bins[i]), "max": float(rf_bins[i + 1])}
            for i, l in enumerate(["Not poor", "Lower-middle", "Upper-middle", "Poorest"])
        ]

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
        cnn_quartiles, cnn_bins = pd.qcut(
            cnn_map.loc[valid_cnn, "predicted_poverty"],
            q=4,
            labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
            retbins=True,
            duplicates="drop",
        )
        cnn_map.loc[valid_cnn, "poverty_quartile_cnn"] = cnn_quartiles
        quartile_ranges["cnn"] = [
            {"label": l, "min": float(cnn_bins[i]), "max": float(cnn_bins[i + 1])}
            for i, l in enumerate(["Not poor", "Lower-middle", "Upper-middle", "Poorest"])
        ]

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

    try:
        poor_hh = pd.read_csv(POOR_HOUSEHOLDS_PATH)
        poor_hh = poor_hh[poor_hh["Barangays"].str.upper() != "TOTAL"].copy()
        poor_hh["brgy_key"] = (
            poor_hh["_orig_Barangays"].fillna(poor_hh["Barangays"]).astype(str).str.upper().str.strip()
        )

        roi_with_key = roi_gdf.copy()
        roi_with_key["brgy_key"] = roi_with_key[barangay_col].astype(str).str.upper().str.strip()

        census_merge = roi_with_key.merge(
            poor_hh[
                [
                    "brgy_key",
                    "Total Assessed Households",
                    "Identified Poor Households",
                    "Poverty Magnitude",
                ]
            ],
            on="brgy_key",
            how="left",
        )

        census_cols = [
            barangay_col,
            "geometry",
            "Total Assessed Households",
            "Identified Poor Households",
            "Poverty Magnitude",
        ]
        census_gdf = census_merge[census_cols].rename(
            columns={
                barangay_col: "barangay",
                "Total Assessed Households": "total_households",
                "Identified Poor Households": "poor_households",
                "Poverty Magnitude": "poverty_magnitude",
            }
        )

        valid_census = census_gdf["poverty_magnitude"].notna()
        if valid_census.any():
            census_quartiles, census_bins = pd.qcut(
                census_gdf.loc[valid_census, "poverty_magnitude"],
                q=4,
                labels=["Not poor", "Lower-middle", "Upper-middle", "Poorest"],
                retbins=True,
                duplicates="drop",
            )
            census_gdf.loc[valid_census, "poverty_quartile_census"] = census_quartiles
            quartile_ranges["census_households"] = [
                {"label": l, "min": float(census_bins[i]), "max": float(census_bins[i + 1])}
                for i, l in enumerate(["Not poor", "Lower-middle", "Upper-middle", "Poorest"])
            ]

        census_poverty_geojson = _to_geojson_dict(census_gdf)
    except Exception:
        census_poverty_geojson = None

    boundary_geojson = _to_geojson_dict(roi_gdf)

    if barangay_col and barangay_col in roi_gdf.columns:
        brgy_labels_gdf = roi_gdf[[barangay_col, "geometry"]].copy()
        brgy_labels_gdf = brgy_labels_gdf.rename(columns={barangay_col: "barangay"})
    else:
        brgy_labels_gdf = roi_gdf[["geometry"]].copy()
        brgy_labels_gdf["barangay"] = None

    if brgy_labels_gdf.crs is not None and not brgy_labels_gdf.crs.is_projected:
        try:
            projected = brgy_labels_gdf.to_crs(brgy_labels_gdf.estimate_utm_crs())
            centroids_proj = projected.geometry.centroid
            centroids = centroids_proj.to_crs(brgy_labels_gdf.crs)
        except Exception:
            centroids = brgy_labels_gdf.geometry.centroid
    else:
        centroids = brgy_labels_gdf.geometry.centroid

    brgy_labels_gdf["geometry"] = centroids
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
        "censusPoverty": census_poverty_geojson,
        "quartileRanges": quartile_ranges,
    }


_data_cache: dict | None = None
_stats_cache: dict | None = None


def get_data() -> dict:
    global _data_cache
    if _data_cache is None:
        _data_cache = _prepare_data()
    return _data_cache


def _prepare_statistics() -> dict:
    stats: dict = {}

    hh = pd.read_csv(POOR_HOUSEHOLDS_PATH)
    hh = hh[hh["Barangays"].str.upper() != "TOTAL"].copy()
    hh_sorted = hh.sort_values("Poverty Magnitude", ascending=False).head(10)
    names = hh_sorted["_orig_Barangays"].fillna(hh_sorted["Barangays"]).astype(str).tolist()
    stats["top_poverty_households"] = {
        "barangays": names,
        "poverty_magnitude": hh_sorted["Poverty Magnitude"].astype(float).tolist(),
        "poor_households": hh_sorted["Identified Poor Households"].astype(int).tolist(),
        "total_households": hh_sorted["Total Assessed Households"].astype(int).tolist(),
    }

    children = pd.read_csv(POOR_CHILDREN_PATH)
    children = children[children["Barangay"].str.upper() != "TOTAL"].copy()
    total_children = float(children["Total Number of Poor Children"].sum())
    attending = float(children["Children Attending School"].sum())
    not_attending = float(children["Children Not Attending School"].sum())
    stats["poor_children_attendance"] = {
        "total_children": total_children,
        "attending": attending,
        "not_attending": not_attending,
    }

    children_sorted = children.sort_values(
        "Children Not Attending School", ascending=False
    ).head(10)
    stats["top_poor_children_not_attending"] = {
        "barangays": children_sorted["Barangay"].astype(str).tolist(),
        "not_attending": children_sorted["Children Not Attending School"].astype(float).tolist(),
        "total_children": children_sorted["Total Number of Poor Children"].astype(float).tolist(),
    }

    water = pd.read_csv(WATER_SOURCE_PATH)
    water = water[water["Barangay"].str.upper() != "TOTAL"].copy()
    safe_cols = [
        "Own use, Faucet, Community Water System",
        "Shared Faucet, Community Water System",
        "Own Use, Tubed/Piped Deep Well",
        "Shared, Tubed/Piped Deep Well",
        "Protected Spring",
    ]
    less_safe_cols = [
        "Tubed/Piped Shallow Well",
        "Dug Well",
        "Unprotected Spring",
    ]
    surface_cols = ["Lake, River, Rain"]
    peddler_cols = ["Peddler_"]
    other_cols = ["Others_"]

    def _sum_cols(df: pd.DataFrame, cols: list[str]) -> float:
        existing = [c for c in cols if c in df.columns]
        if not existing:
            return 0.0
        return float(df[existing].sum().sum())

    stats["water_source"] = {
        "labels": [
            "Piped / protected sources",
            "Wells & unprotected springs",
            "Surface water (lake/river/rain)",
            "Water peddlers",
            "Other sources",
        ],
        "counts": [
            _sum_cols(water, safe_cols),
            _sum_cols(water, less_safe_cols),
            _sum_cols(water, surface_cols),
            _sum_cols(water, peddler_cols),
            _sum_cols(water, other_cols),
        ],
    }

    emp = pd.read_csv(POOR_EMPLOYED_PATH)
    emp = emp[emp["Barangay"].str.upper() != "TOTAL"].copy()
    occ_cols = [
        "Managers",
        "Gov't and Special Interest Organization Officials",
        "Professionals",
        "Technicians",
        "Clerks",
        "Service and Sales Workers",
        "Farm Workers, Foresters and Fisher Folks",
        "Crafts and Related Workers",
        "Plants and Machine Operators and Assemblers",
        "Laborers and Unskilled Workers",
    ]
    existing_occ = [c for c in occ_cols if c in emp.columns]
    occ_totals = emp[existing_occ].sum().astype(float)
    stats["poor_employment_occupation"] = {
        "labels": existing_occ,
        "counts": occ_totals.tolist(),
    }

    def _norm_name(val: object) -> str:
        if pd.isna(val):
            return ""
        return str(val).strip().upper()

    def _row_sum(row: pd.Series, cols: list[str]) -> float:
        existing_cols = [c for c in cols if c in row.index]
        if not existing_cols:
            return 0.0
        return float(row[existing_cols].sum())

    hh_local = hh.copy()
    hh_local["name_display"] = hh_local["_orig_Barangays"].fillna(hh_local["Barangays"]).astype(str)
    hh_local["key"] = hh_local["name_display"].map(_norm_name)
    hh_map = {row["key"]: row for _, row in hh_local.iterrows()}

    children_local = children.copy()
    children_local["name_display"] = children_local["_orig_Barangay"].fillna(
        children_local["Barangay"]
    ).astype(str)
    children_local["key"] = children_local["name_display"].map(_norm_name)
    children_map = {row["key"]: row for _, row in children_local.iterrows()}

    water_local = water.copy()
    water_local["name_display"] = water_local["_orig_Barangay"].fillna(
        water_local["Barangay"]
    ).astype(str)
    water_local["key"] = water_local["name_display"].map(_norm_name)
    water_map = {row["key"]: row for _, row in water_local.iterrows()}

    emp_local = emp.copy()
    emp_local["name_display"] = emp_local["_orig_Barangay"].fillna(emp_local["Barangay"]).astype(
        str
    )
    emp_local["key"] = emp_local["name_display"].map(_norm_name)
    emp_map = {row["key"]: row for _, row in emp_local.iterrows()}

    all_keys: set[str] = set(hh_map) | set(children_map) | set(water_map) | set(emp_map)

    barangay_factors: dict[str, dict] = {}

    water_numeric_cols = [
        c
        for c in water.columns
        if c not in ("Barangay", "_orig_Barangay") and pd.api.types.is_numeric_dtype(water[c])
    ]

    vulnerable_occ_cols = [
        "Service and Sales Workers",
        "Farm Workers, Foresters and Fisher Folks",
        "Plants and Machine Operators and Assemblers",
        "Laborers and Unskilled Workers",
    ]

    for key in all_keys:
        base_row = hh_map.get(key)
        if base_row is None:
            base_row = children_map.get(key)
        if base_row is None:
            base_row = water_map.get(key)
        if base_row is None:
            base_row = emp_map.get(key)
        if base_row is None:
            continue

        name_display = str(base_row["name_display"]).strip()
        rec: dict[str, float | int | str | None] = {"name": name_display}

        if key in hh_map:
            row = hh_map[key]
            try:
                total_hh = float(row["Total Assessed Households"])
                poor_hh = float(row["Identified Poor Households"])
                poverty_mag = row.get("Poverty Magnitude")
                rec["total_households"] = total_hh
                rec["poor_households"] = poor_hh
                if pd.notna(poverty_mag):
                    rec["poverty_households_pct"] = float(poverty_mag) * 100.0
                else:
                    rec["poverty_households_pct"] = None
            except Exception:
                rec["poverty_households_pct"] = None

        if key in children_map:
            row = children_map[key]
            try:
                total_children_row = float(row["Total Number of Poor Children"])
                not_att_row = float(row["Children Not Attending School"])
                rec["total_poor_children"] = total_children_row
                rec["children_not_attending"] = not_att_row
                if total_children_row > 0:
                    rec["children_not_attending_pct"] = (not_att_row / total_children_row) * 100.0
                else:
                    rec["children_not_attending_pct"] = None
            except Exception:
                rec["children_not_attending_pct"] = None

        if key in water_map:
            row = water_map[key]
            try:
                total_ws = float(row[water_numeric_cols].sum()) if water_numeric_cols else 0.0
                safe_ws = _row_sum(row, safe_cols)
                unsafe_ws = max(total_ws - safe_ws, 0.0)
                rec["total_water_households"] = total_ws
                rec["unsafe_water_households"] = unsafe_ws
                if total_ws > 0:
                    rec["unsafe_water_households_pct"] = (unsafe_ws / total_ws) * 100.0
                else:
                    rec["unsafe_water_households_pct"] = None
            except Exception:
                rec["unsafe_water_households_pct"] = None

        if key in emp_map:
            row = emp_map[key]
            try:
                total_emp = float(row["Total Number of Poor Employed"])
                vuln_count = _row_sum(row, vulnerable_occ_cols)
                rec["total_poor_employed"] = total_emp
                rec["vulnerable_jobs_employed"] = vuln_count
                if total_emp > 0:
                    rec["vulnerable_jobs_pct"] = (vuln_count / total_emp) * 100.0
                else:
                    rec["vulnerable_jobs_pct"] = None
            except Exception:
                rec["vulnerable_jobs_pct"] = None

        barangay_factors[name_display] = rec

    stats["barangay_factors"] = barangay_factors
    stats["barangay_list"] = sorted(barangay_factors.keys())

    return stats


def get_statistics() -> dict:
    global _stats_cache
    if _stats_cache is None:
        _stats_cache = _prepare_statistics()
    return _stats_cache


def _load_grid_predictions() -> tuple[dict, dict, dict]:
    """Load real per-cell predictions and build GeoJSON for CatBoost/RF/CNN.

    Uses:
      - data/grid_with_comprehensive_data.csv for geometry and barangay names
      - data/grid_predictions_comparison.csv for CatBoost/RF scaled predictions
      - data/all_cells_predictions_1km.csv for CNN per-cell predictions

    Returns (boundary_fc, labels_fc, models_dict) where models_dict has
    'catboost', 'rf', and 'cnn' FeatureCollections.
    """
    grid_csv = DATA_DIR / "grid_with_comprehensive_data.csv"
    preds_csv = DATA_DIR / "grid_predictions_comparison.csv"
    cnn_csv = DATA_DIR / "all_cells_predictions_1km.csv"

    if not (grid_csv.exists() and preds_csv.exists() and cnn_csv.exists()):
        raise FileNotFoundError("Required data CSV files are missing in data/.")

    grid_df = pd.read_csv(grid_csv)
    preds_df = pd.read_csv(preds_csv)
    cnn_df = pd.read_csv(cnn_csv)

    # Merge on grid_id, keep geometry (.geo) and barangay name
    cols_needed = [
        "grid_id",
        ".geo",
        "lon",
        "lat",
        "barangay_name_clean",
    ]
    missing = [c for c in cols_needed if c not in grid_df.columns]
    if missing:
        raise KeyError(f"Missing columns in grid_with_comprehensive_data.csv: {missing}")

    merged = (
        grid_df[cols_needed]
        .merge(preds_df, on="grid_id", how="inner")
        .dropna(subset=["pred_scaled_catboost", "pred_scaled_rf"])
        .reset_index(drop=True)
    )

    # Convert scaled predictions (0-1) to percentages
    merged["poverty_pct_catboost"] = merged["pred_scaled_catboost"] * 100.0
    merged["poverty_pct_rf"] = merged["pred_scaled_rf"] * 100.0

    labels = ["Not poor", "Lower-middle", "Upper-middle", "Poorest"]
    # Quartiles for each model
    merged["poverty_quartile_catboost"] = pd.qcut(
        merged["pred_scaled_catboost"],
        q=4,
        labels=labels,
        duplicates="drop",
    )
    merged["poverty_quartile_rf"] = pd.qcut(
        merged["pred_scaled_rf"],
        q=4,
        labels=labels,
        duplicates="drop",
    )

    try:
        if not SHAPEFILE_PATH.exists():
            raise FileNotFoundError

        roi_gdf = gpd.read_file(SHAPEFILE_PATH)

        if "adm4_en" in roi_gdf.columns:
            barangay_col = "adm4_en"
        else:
            barangay_col = None
            for col in roi_gdf.columns:
                lower = str(col).lower()
                if "brgy" in lower or "barangay" in lower or "bgy" in lower:
                    barangay_col = col
                    break

        if barangay_col:
            roi_gdf = roi_gdf[roi_gdf[barangay_col].notna()].copy()

            try:
                minx, miny, maxx, maxy = roi_gdf.total_bounds

                def _is_full_extent_box(geom) -> bool:
                    if geom is None or geom.is_empty:
                        return False
                    gx_minx, gx_miny, gx_maxx, gx_maxy = geom.bounds
                    tol = 1e-6
                    return (
                        abs(gx_minx - minx) < tol
                        and abs(gx_miny - miny) < tol
                        and abs(gx_maxx - maxx) < tol
                        and abs(gx_maxy - maxy) < tol
                    )

                extent_mask = roi_gdf.geometry.apply(_is_full_extent_box)
                if extent_mask.any():
                    roi_gdf = roi_gdf.loc[~extent_mask].copy()
            except Exception:
                pass

            if roi_gdf.crs is not None and roi_gdf.crs.to_epsg() != 4326:
                roi_gdf = roi_gdf.to_crs(epsg=4326)

            roi_gdf = roi_gdf[[barangay_col, "geometry"]].rename(columns={barangay_col: "barangay"})
            boundary_fc = json.loads(roi_gdf.to_json(drop_id=True))
        else:
            raise ValueError("No barangay column found in admin boundaries shapefile")
    except Exception:
        min_lon = float(merged["lon"].min())
        max_lon = float(merged["lon"].max())
        min_lat = float(merged["lat"].min())
        max_lat = float(merged["lat"].max())

        boundary_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": "Zamboanga City"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [min_lon, min_lat],
                                [max_lon, min_lat],
                                [max_lon, max_lat],
                                [min_lon, max_lat],
                                [min_lon, min_lat],
                            ]
                        ],
                    },
                }
            ],
        }

    # Barangay label points: centroid approximated by mean lon/lat per barangay
    labels_features = []
    if "barangay_name_clean" in merged.columns:
        for brgy, g in merged.groupby("barangay_name_clean"):
            lon_mean = float(g["lon"].mean())
            lat_mean = float(g["lat"].mean())
            labels_features.append(
                {
                    "type": "Feature",
                    "properties": {"barangay": brgy},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [lon_mean, lat_mean],
                    },
                }
            )

    barangay_labels_fc = {
        "type": "FeatureCollection",
        "features": labels_features,
    }

    # Build per-model FeatureCollections. Each model gets its own FC so that
    # the shared poverty_pct field reflects the active model.
    cat_features = []
    rf_features = []
    cnn_features = []

    # Prepare CNN join: parse indices from cell_id and merge to grid indices
    if {"cell_id", "predicted_poverty"}.issubset(cnn_df.columns) and {"x_idx_x", "y_idx_x"}.issubset(grid_df.columns):
        idx_df = cnn_df.copy()

        def _parse_cell_id(cell: str) -> tuple[int, int] | tuple[None, None]:
            try:
                parts = str(cell).split("_")
                # Expect format like "cell_0000_0021"
                if len(parts) != 3:
                    return None, None
                x = int(parts[1])
                y = int(parts[2])
                return x, y
            except Exception:
                return None, None

        idx_df[["x_idx_x", "y_idx_x"]] = idx_df["cell_id"].apply(
            lambda c: pd.Series(_parse_cell_id(c))
        )

        cnn_join = (
            grid_df[["grid_id", "x_idx_x", "y_idx_x"]]
            .merge(idx_df, on=["x_idx_x", "y_idx_x"], how="inner")
            .dropna(subset=["predicted_poverty"])
        )

        cnn_join["poverty_pct_cnn"] = cnn_join["predicted_poverty"] * 100.0

        # Align CNN back to the merged RF/CatBoost rows via grid_id
        merged = merged.merge(
            cnn_join[["grid_id", "poverty_pct_cnn"]],
            on="grid_id",
            how="left",
        )

        # CNN quartiles (only where CNN prediction exists)
        if merged["poverty_pct_cnn"].notna().any():
            merged["poverty_quartile_cnn"] = pd.qcut(
                merged["poverty_pct_cnn"].dropna() / 100.0,
                q=4,
                labels=labels,
                duplicates="drop",
            ).reindex(merged.index)
        else:
            merged["poverty_quartile_cnn"] = pd.NA
    else:
        merged["poverty_pct_cnn"] = pd.NA
        merged["poverty_quartile_cnn"] = pd.NA
    for _, row in merged.iterrows():
        geom = json.loads(row[".geo"])
        brgy = row["barangay_name_clean"]

        cat_features.append(
            {
                "type": "Feature",
                "properties": {
                    "grid_id": row["grid_id"],
                    "barangay": brgy,
                    "poverty_pct": float(row["poverty_pct_catboost"]),
                    "poverty_quartile_catboost": str(row["poverty_quartile_catboost"]),
                },
                "geometry": geom,
            }
        )

        if pd.notna(row.get("poverty_pct_cnn")):
            cnn_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "grid_id": row["grid_id"],
                        "barangay": brgy,
                        "poverty_pct": float(row["poverty_pct_cnn"]),
                        "poverty_quartile_cnn": str(row.get("poverty_quartile_cnn")),
                    },
                    "geometry": geom,
                }
            )

        rf_features.append(
            {
                "type": "Feature",
                "properties": {
                    "grid_id": row["grid_id"],
                    "barangay": brgy,
                    "poverty_pct": float(row["poverty_pct_rf"]),
                    "poverty_quartile_rf": str(row["poverty_quartile_rf"]),
                },
                "geometry": geom,
            }
        )

    models = {
        "catboost": {"type": "FeatureCollection", "features": cat_features},
        "rf": {"type": "FeatureCollection", "features": rf_features},
        "cnn": {"type": "FeatureCollection", "features": cnn_features} if cnn_features else None,
    }

    return boundary_fc, barangay_labels_fc, models


@app.route("/api/predictions")
def api_predictions() -> object:
    """Serve full prediction payload matching the contract used by static/app.js."""
    try:
        data = get_data()
        return jsonify(data)
    except Exception as exc:  # pragma: no cover - logged via JSON
        return jsonify(
            {
                "boundary": None,
                "barangayLabels": None,
                "models": {"catboost": None, "rf": None, "cnn": None},
                "censusPoverty": None,
                "quartileRanges": {},
                "error": str(exc),
            }
        )


@app.route("/api/statistics")
def api_statistics() -> object:
    """Serve city-level and barangay-level statistics for the Statistics tab."""
    try:
        data = get_statistics()
        return jsonify(data)
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh() -> object:
    """Trigger a refresh of prediction layers from upstream data sources.

    This is a safe placeholder hook for the full GEE + WorldPop + OSM + model
    pipeline. In production you would call the orchestration code here, e.g.:

      - Run the quarterly GEE extraction pipeline
      - Pull / preprocess WorldPop & OSM covariates
      - Regenerate model features and run CatBoost/RF/CNN inference
      - Write updated CSV/GeoJSON artifacts under data/

    For now, this endpoint simply reports success so the UI wiring can be
    validated end-to-end.
    """
    try:
        # Placeholder: no-op refresh.
        return jsonify(
            {
                "success": True,
                "message": "Refresh hook invoked (placeholder – plug in full pipeline here).",
            }
        )
    except Exception as exc:  # pragma: no cover
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=8000, debug=False)
