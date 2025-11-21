from pathlib import Path
import json

import pandas as pd
import geopandas as gpd
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

GRID_GEOJSON_PATH = BASE_DIR / "data" / "grid_with_comprehensive_data.geojson"
MERGED_PREDICTIONS_PATH = BASE_DIR / "data" / "grid_predictions_comparison.csv"
GRID_GPKG_PATH = BASE_DIR / "data" / "grid_1km_all.gpkg"
CNN_PRED_PATH = BASE_DIR / "data" / "all_cells_predictions_1km.csv"
SHAPEFILE_PATH = BASE_DIR / "data" / "shapefile" / "zc04AdminBoundaries_gcs.shp"

# CSV-based census and socio-economic data
CSV_DIR = BASE_DIR / "csv_outputs"
POOR_HOUSEHOLDS_PATH = CSV_DIR / "Number of Poor Households.clean.csv"
POOR_INDIVIDUALS_PATH = CSV_DIR / "Number of Poor Individuals.clean.csv"
POOR_CHILDREN_PATH = CSV_DIR / "Poor Children Attending and Not.clean.csv"
WATER_SOURCE_PATH = CSV_DIR / "Water Source.clean.csv"
EDU_ATTAIN_PATH = CSV_DIR / "Educational Attainment.clean.csv"
POOR_EMPLOYED_PATH = CSV_DIR / "Total Number of Poor Employed _.clean.csv"

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

    # Calculate quartiles and value ranges for legend
    quartile_ranges = {}

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
            {"label": l, "min": float(cat_bins[i]), "max": float(cat_bins[i+1])}
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
            {"label": l, "min": float(rf_bins[i]), "max": float(rf_bins[i+1])}
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
            {"label": l, "min": float(cnn_bins[i]), "max": float(cnn_bins[i+1])}
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

    # --- Barangay-level census poverty layer (household-based) ---
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

    # Prepare barangay label points (centroids) for map overlays
    if barangay_col and barangay_col in roi_gdf.columns:
        brgy_labels_gdf = roi_gdf[[barangay_col, "geometry"]].copy()
        brgy_labels_gdf = brgy_labels_gdf.rename(columns={barangay_col: "barangay"})
    else:
        brgy_labels_gdf = roi_gdf[["geometry"]].copy()
        brgy_labels_gdf["barangay"] = None

    # Compute centroids in a projected CRS to avoid inaccurate results in
    # geographic (lat/lon) CRS, then transform back.
    if brgy_labels_gdf.crs is not None and not brgy_labels_gdf.crs.is_projected:
        try:
            projected = brgy_labels_gdf.to_crs(brgy_labels_gdf.estimate_utm_crs())
            centroids_proj = projected.geometry.centroid
            centroids = centroids_proj.to_crs(brgy_labels_gdf.crs)
        except Exception:
            # Fallback: compute in-place centroids even if CRS is geographic
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
    """Prepare city-level statistics for the Statistics tab from CSVs."""

    stats: dict = {}

    # --- Top barangays by census poverty magnitude (households) ---
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

    # --- Poor children attending vs not attending school (city-wide) ---
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
        "not_attending": children_sorted["Children Not Attending School"]
        .astype(float)
        .tolist(),
        "total_children": children_sorted["Total Number of Poor Children"]
        .astype(float)
        .tolist(),
    }

    # --- Water source composition among poor households (city-wide, grouped) ---
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

    # --- Employment profile of poor workers by occupation (city-wide) ---
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

    # --- Barangay-level factors for the "Barangay factors" diagram ---
    def _norm_name(val: object) -> str:
        if pd.isna(val):
            return ""
        return str(val).strip().upper()

    def _row_sum(row: pd.Series, cols: list[str]) -> float:
        existing_cols = [c for c in cols if c in row.index]
        if not existing_cols:
            return 0.0
        return float(row[existing_cols].sum())

    # Align barangay names across tables using their original name columns
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

    # Pre-compute which columns in water are numeric counts
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
        # Select a representative base row without relying on Series truthiness
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

        # Poverty among households
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

        # Poor children not attending school
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

        # Households using unsafe water sources
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

        # Poor workers in vulnerable jobs
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


@app.get("/")
async def root() -> FileResponse:
    index_path = static_dir / "index.html"
    return FileResponse(str(index_path))


@app.get("/api/predictions")
async def get_predictions() -> JSONResponse:
    data = get_data()
    return JSONResponse(content=data)


@app.get("/api/statistics")
async def get_statistics_endpoint() -> JSONResponse:
    data = get_statistics()
    return JSONResponse(content=data)
