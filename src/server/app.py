from __future__ import annotations

from pathlib import Path
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timedelta

import pandas as pd
import geopandas as gpd
from flask import (
    Flask,
    jsonify,
    send_from_directory,
    redirect,
    url_for,
    session,
    request,
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# Add project root to Python path so we can import from src
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
SHAPEFILE_PATH = DATA_DIR / "shapefile" / "zc04AdminBoundaries_gcs.shp"

GRID_GEOJSON_PATH = DATA_DIR / "grid_1km_all.gpkg"
MERGED_PREDICTIONS_PATH = DATA_DIR / "gpkg_complete_predictions.csv"
GRID_GPKG_PATH = DATA_DIR / "grid_1km_all.gpkg"
CNN_PRED_PATH = DATA_DIR / "all_cells_predictions_1km.csv"

CSV_DIR = ROOT / "csv_outputs"
POOR_HOUSEHOLDS_PATH = CSV_DIR / "Number of Poor Households.clean.csv"
POOR_INDIVIDUALS_PATH = CSV_DIR / "Number of Poor Individuals.clean.csv"
POOR_CHILDREN_PATH = CSV_DIR / "Poor Children Attending and Not.clean.csv"
WATER_SOURCE_PATH = CSV_DIR / "Water Source.clean.csv"
EDU_ATTAIN_PATH = CSV_DIR / "Educational Attainment.clean.csv"
POOR_EMPLOYED_PATH = CSV_DIR / "Total Number of Poor Employed _.clean.csv"
SHEETS_SUMMARY_PATH = CSV_DIR / "sheets_saved_summary.csv"
SHEETS_CONFIG_PATH = CSV_DIR / "sheets_chart_config.json"

USERS_DB_PATH = DATA_DIR / "users.db"

# Refresh pipeline configuration (all paths inside this workspace)
SCRIPTS_DIR = ROOT / "scripts"  # Contains GEE extraction, preprocessing, training scripts
ASSETS_DIR = ROOT / "assets"    # Contains shapefiles, socioeconomic CSVs
OUTPUT_DIR = ROOT / "output"    # Contains model outputs
GEE_EXPORTS_DIR = ROOT / "googleEarthExports"  # GEE data exports
MODELS_DIR = ROOT / "models"
REFRESH_COOLDOWN_DAYS = 90  # Warn if refresh less than this many days ago
MAX_DATE_RANGE_DAYS = 365  # Maximum date range for data collection

load_dotenv()

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-change-me")


def _resolve_csv_path(csv_rel: str) -> Path | None:
    """Resolve a CSV path from sheets_saved_summary.csv to an actual file path.
    
    Handles:
    - Windows backslashes in paths
    - Paths like 'csv_outputs/filename.csv'
    - Fallback to .clean.csv versions
    """
    if not csv_rel:
        return None
    
    # Normalize path separators
    csv_rel = csv_rel.replace("\\", "/")
    
    # Try the full relative path from ROOT
    csv_path = ROOT / csv_rel
    if csv_path.exists():
        return csv_path
    
    # Extract just the filename
    csv_filename = Path(csv_rel).name
    
    # Try in CSV_DIR
    alt = CSV_DIR / csv_filename
    if alt.exists():
        return alt
    
    # Try .clean.csv version
    if csv_filename.endswith(".csv") and not csv_filename.endswith(".clean.csv"):
        clean_name = csv_filename.replace(".csv", ".clean.csv")
        clean_path = CSV_DIR / clean_name
        if clean_path.exists():
            return clean_path
    
    return None


# Global error handler to log all exceptions
@app.errorhandler(Exception)
def handle_exception(e):
    """Log all exceptions for debugging."""
    import traceback
    print(f"ERROR: {type(e).__name__}: {str(e)}", flush=True)
    print(traceback.format_exc(), flush=True)
    return jsonify({"error": str(e), "type": type(e).__name__}), 500


def _get_db_connection() -> sqlite3.Connection:
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_users_db() -> None:
    conn = _get_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                barangay TEXT,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS refresh_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'running',
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                triggered_by TEXT,
                error_message TEXT,
                predictions_backup_path TEXT,
                elapsed_seconds REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                suppress_refresh_warning INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

_init_users_db()


def _get_current_user() -> dict | None:
    username = session.get("username")
    if not username:
        return None
    return {"username": username}


def _is_valid_email(value: str) -> bool:
    if not value:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


@app.route("/")
def landing() -> object:  # pragma: no cover
    return send_from_directory(app.static_folder, "landing.html")


@app.route("/admin")
def admin() -> object:  # pragma: no cover
    if _get_current_user() is None:
        return redirect(url_for("login"))
    return send_from_directory(app.static_folder, "index.html")


@app.route("/admin/data")
def admin_data() -> object:  # pragma: no cover
    if _get_current_user() is None:
        return redirect(url_for("login"))
    return send_from_directory(app.static_folder, "admin_data.html")


@app.route("/login")
def login() -> object:  # pragma: no cover
    return send_from_directory(app.static_folder, "login.html")


@app.route("/auth/register", methods=["POST"])
def auth_register() -> object:
    # Only allow an authenticated admin to create new users
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password are required."}), 400

    password_hash = generate_password_hash(password)

    conn = _get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (username, password_hash)
            VALUES (?, ?)
            """,
            (username, password_hash),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Username already exists."}), 400
    finally:
        conn.close()

    # Do not change the current logged-in admin session
    return jsonify({"success": True, "username": username}), 201


@app.route("/auth/login", methods=["POST"])
def auth_login() -> object:
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not username or not password:
        accept_header = request.headers.get("Accept") or ""
        if "application/json" in accept_header:
            return (
                jsonify(
                    {"success": False, "error": "Username and password are required."}
                ),
                400,
            )
        return redirect(url_for("login", error="missing"))

    conn = _get_db_connection()
    try:
        cur = conn.execute(
            "SELECT username, password_hash FROM users WHERE username = ?",
            (username,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if row is None or not check_password_hash(row["password_hash"], password):
        accept_header = request.headers.get("Accept") or ""
        if "application/json" in accept_header:
            return jsonify({"success": False, "error": "Invalid credentials."}), 401
        return redirect(url_for("login", error="invalid"))

    session["username"] = username
    return redirect(url_for("admin"))


@app.route("/logout")
def logout() -> object:  # pragma: no cover
    session.clear()  # Clear entire session
    response = redirect(url_for("landing"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/api/me")
def api_me() -> object:
    user = _get_current_user()
    if not user:
        return jsonify({"user": None})
    return jsonify({"user": user})


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

    # Use GPKG for complete coverage
    grid_gdf = gpd.read_file(GRID_GEOJSON_PATH)  # This is now the GPKG
    merged = pd.read_csv(MERGED_PREDICTIONS_PATH)  # This is now the complete predictions
    
    # Convert GPKG cell_id to grid_id format for merging
    def cell_id_to_grid_id(cell_id):
        try:
            parts = str(cell_id).split('_')
            if len(parts) == 3 and parts[0] == 'cell':
                x = int(parts[1])
                y = int(parts[2])
                return f"{x}_{y}"
        except:
            pass
        return cell_id  # Return original if conversion fails

    if 'cell_id' in grid_gdf.columns and 'grid_id' not in grid_gdf.columns:
        grid_gdf['grid_id'] = grid_gdf['cell_id'].apply(cell_id_to_grid_id)
    
    gdf = grid_gdf.merge(merged, on="grid_id", how="left")

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


def _load_sheets_summary() -> pd.DataFrame:
    if not SHEETS_SUMMARY_PATH.exists():
        return pd.DataFrame(
            columns=["sheet_name", "safe_name", "rows", "columns", "csv_path"]
        )

    try:
        df = pd.read_csv(SHEETS_SUMMARY_PATH)
    except Exception:
        df = pd.DataFrame(
            columns=["sheet_name", "safe_name", "rows", "columns", "csv_path"]
        )

    for col in ("rows", "columns"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


def _save_sheets_summary(df: pd.DataFrame) -> None:
    SHEETS_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SHEETS_SUMMARY_PATH, index=False)


def _slugify_sheet_name(name: str) -> str:
    value = (name or "").strip()
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = value.strip("_")
    return value or "sheet"


def _load_sheets_config() -> dict:
    if not SHEETS_CONFIG_PATH.exists():
        return {}
    try:
        with SHEETS_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_sheets_config(config: dict) -> None:
    SHEETS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHEETS_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _load_sheet_dataframe_from_summary(name_hint: str) -> pd.DataFrame | None:
    summary = _load_sheets_summary()
    if summary.empty:
        return None

    key_col = "sheet_name" if "sheet_name" in summary.columns else None
    mask = None
    if key_col is not None:
        mask = (
            summary[key_col]
            .astype(str)
            .str.strip()
            .str.upper()
            == (name_hint or "").strip().upper()
        )

    if (mask is None or not mask.any()) and "safe_name" in summary.columns:
        slug = _slugify_sheet_name(name_hint)
        mask = (
            summary["safe_name"].astype(str).str.strip().str.upper() == slug.upper()
        )

    if mask is None or not mask.any():
        return None

    row = summary.loc[mask].iloc[0]
    csv_rel = str(row.get("csv_path") or "").strip()
    csv_path = _resolve_csv_path(csv_rel)
    
    if csv_path is None:
        return None

    try:
        return pd.read_csv(csv_path)
    except Exception:
        return None


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

    try:
        nature_df = _load_sheet_dataframe_from_summary("Nature of Employment")
        if nature_df is not None and not nature_df.empty:
            emp_cols = [
                col
                for col in ("Permanent", "Seasonal/ Short Term", "Weekly Basis")
                if col in nature_df.columns
            ]
            if emp_cols:
                totals = nature_df[emp_cols].sum(numeric_only=True).astype(float)
                stats["nature_of_employment"] = {
                    "labels": list(emp_cols),
                    "counts": totals.tolist(),
                }
    except Exception:
        pass

    # Build a list of custom sheet visualizations based on admin-configured
    # quick visualization settings for sheets that are explicitly exposed in
    # the Statistics tab.
    custom_sheets: list[dict] = []
    try:
        cfg_all = _load_sheets_config()
        summary = _load_sheets_summary()
        if not summary.empty and isinstance(cfg_all, dict):
            # Normalize summary columns for joins
            if "safe_name" not in summary.columns:
                summary["safe_name"] = summary.get("sheet_name", "").apply(_slugify_sheet_name)

            for safe_name, cfg_entry in cfg_all.items():
                if not isinstance(cfg_entry, dict):
                    continue
                if not cfg_entry.get("expose_in_statistics"):
                    continue

                chart_type = (cfg_entry.get("chart_type") or "bar").strip().lower()
                if chart_type not in {"bar", "line", "pie"}:
                    chart_type = "bar"

                x_column = (cfg_entry.get("x_column") or "").strip()
                y_columns = cfg_entry.get("y_columns") or []
                if not x_column or not y_columns:
                    continue

                y_column = str(y_columns[0])

                row_mode = (cfg_entry.get("row_mode") or "top5").strip().lower()
                if row_mode not in {"top5", "all", "single"}:
                    row_mode = "top5"

                sort_column = (cfg_entry.get("sort_column") or "").strip() or None
                filter_mode = (cfg_entry.get("filter_mode") or "").strip().lower() or None
                filter_value = (cfg_entry.get("filter_value") or "").strip() or None

                # Find the sheet metadata row
                row_mask = summary["safe_name"].astype(str) == str(safe_name)
                if not row_mask.any():
                    continue
                row = summary.loc[row_mask].iloc[0]
                csv_rel = str(row.get("csv_path") or "").strip()
                csv_path = _resolve_csv_path(csv_rel)
                if csv_path is None:
                    continue

                try:
                    df = pd.read_csv(csv_path)
                except Exception:
                    continue

                # Validate that X column and all Y columns exist
                if x_column not in df.columns:
                    continue
                
                # Check if all Y columns exist (for bar charts with multiple Y-axis)
                missing_y_cols = [col for col in y_columns if col not in df.columns]
                if missing_y_cols:
                    # Skip if any Y column is missing
                    continue

                # Replace NaN with None for safer JSON encoding later
                if not df.empty:
                    df = df.where(pd.notnull(df), None)

                working = df.copy()
                # Drop rows where X is missing or is a TOTAL row
                x_vals = working[x_column].astype(str)
                mask_valid = x_vals.notna() & (x_vals.str.strip().str.upper() != "TOTAL")
                working = working.loc[mask_valid].copy()
                if working.empty:
                    continue

                # Apply filter for single-barangay mode if configured
                if row_mode == "single" and filter_mode == "x_equals" and filter_value:
                    working = working.loc[
                        working[x_column].astype(str).str.strip() == filter_value
                    ].copy()
                    if working.empty:
                        continue

                # For top5/all we can sort by sort_column if provided
                if row_mode in {"top5", "all"} and sort_column and sort_column in working.columns:
                    sort_series = pd.to_numeric(working[sort_column], errors="coerce")
                    working = working.assign(_sort=sort_series).sort_values(
                        by="_sort", ascending=False
                    )

                if row_mode == "top5":
                    working = working.head(5)

                if working.empty:
                    continue

                x_labels = working[x_column].astype(str).tolist()
                y_vals = pd.to_numeric(working[y_column], errors="coerce").fillna(0.0).astype(float)

                sheet_name = str(row.get("sheet_name") or safe_name)

                # For bar charts, also prepare data for additional Y-axis columns
                y_values_multi = []
                if chart_type == "bar" and len(y_columns) > 1:
                    for y_col in y_columns:
                        if y_col in working.columns:
                            y_data = pd.to_numeric(working[y_col], errors="coerce").fillna(0.0).astype(float)
                            y_values_multi.append({
                                "column": str(y_col),
                                "values": y_data.tolist()
                            })

                sheet_dict = {
                    "safe_name": safe_name,
                    "sheet_name": sheet_name,
                    "chart_type": chart_type,
                    "x_labels": x_labels,
                    "y_values": y_vals.tolist(),
                    "x_column": x_column,
                    "y_column": y_column,
                }
                
                # Add multi-Y data if available for bar charts
                if y_values_multi:
                    sheet_dict["y_values_multi"] = y_values_multi

                custom_sheets.append(sheet_dict)
    except Exception:
        # Do not fail statistics entirely if custom sheets cannot be computed
        custom_sheets = []

    stats["custom_sheets"] = custom_sheets

    return stats


def get_statistics() -> dict:
    # Always recompute statistics so updates from admin-managed sheets and
    # toggled custom charts are reflected without needing to restart the
    # backend.
    return _prepare_statistics()


def _smooth_boundary_predictions(
    merged: gpd.GeoDataFrame,
    pred_cols: list[str],
    blend: float = 0.3,
) -> gpd.GeoDataFrame:
    """Reduce prediction discontinuities at barangay boundaries.

    Grid cells that straddle multiple barangay polygons (border cells) tend to
    have predictions biased by cross-boundary spatial feature aggregation.
    This function blends each border cell's prediction toward the mean of its
    4-connected grid neighbours, producing smoother transitions at boundaries
    while preserving the overall prediction distribution.

    Args:
        merged: GeoDataFrame with ``grid_id`` and prediction columns.
        pred_cols: Prediction column names to smooth (e.g. pred_scaled_catboost).
        blend: Fraction of the neighbour mean to mix in for border cells
               (0 = no change, 1 = fully replace with neighbour mean).
    """
    import numpy as np

    # Parse grid indices from grid_id (format "x_y")
    def _parse(gid: str):
        parts = str(gid).split("_")
        return int(parts[0]), int(parts[1])

    merged = merged.copy()
    merged[["_xi", "_yi"]] = pd.DataFrame(
        merged["grid_id"].apply(_parse).tolist(), index=merged.index
    )

    # Identify border cells by checking if a cell's 4-connected neighbours
    # belong to a different barangay.
    brgy_col = "barangay_name_clean"
    if brgy_col not in merged.columns:
        return merged

    cell_brgy = dict(zip(zip(merged["_xi"], merged["_yi"]), merged[brgy_col]))

    def _is_border(row) -> bool:
        x, y = int(row["_xi"]), int(row["_yi"])
        brgy = row[brgy_col]
        if pd.isna(brgy):
            return False
        for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            nb_brgy = cell_brgy.get((x + dx, y + dy))
            if nb_brgy is not None and not pd.isna(nb_brgy) and nb_brgy != brgy:
                return True
        return False

    border_mask = merged.apply(_is_border, axis=1)
    n_border = border_mask.sum()
    if n_border == 0:
        merged.drop(columns=["_xi", "_yi"], inplace=True)
        return merged

    # Build a lookup for fast neighbour value retrieval
    for col in pred_cols:
        if col not in merged.columns:
            continue
        val_lookup: dict[tuple[int, int], float] = dict(
            zip(zip(merged["_xi"], merged["_yi"]), merged[col])
        )

        smoothed = merged[col].copy()
        for idx in merged.index[border_mask]:
            x, y = int(merged.at[idx, "_xi"]), int(merged.at[idx, "_yi"])
            neighbours = []
            for dx, dy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                nv = val_lookup.get((x + dx, y + dy))
                if nv is not None and np.isfinite(nv):
                    neighbours.append(nv)
            if neighbours:
                nb_mean = float(np.mean(neighbours))
                orig = float(merged.at[idx, col])
                smoothed.at[idx] = orig * (1 - blend) + nb_mean * blend

        merged[col] = smoothed

    merged.drop(columns=["_xi", "_yi"], inplace=True)
    print(f"[boundary smoothing] blended {n_border} border cells (blend={blend})")
    return merged


def _load_grid_predictions() -> tuple[dict, dict, dict]:
    """Load real per-cell predictions and build GeoJSON for CatBoost/RF/CNN.

    Uses:
      - data/grid_with_comprehensive_data.csv for geometry and barangay names
      - data/grid_predictions_comparison.csv for CatBoost/RF scaled predictions
      - data/all_cells_predictions_1km.csv for CNN per-cell predictions

    Returns (boundary_fc, labels_fc, models_dict) where models_dict has
    'catboost', 'rf', and 'cnn' FeatureCollections.
    """
    grid_gpkg = DATA_DIR / "grid_1km_all.gpkg"
    preds_csv = DATA_DIR / "complete_grid_predictions.csv"
    cnn_csv = DATA_DIR / "all_cells_predictions_1km.csv"

    if not (grid_gpkg.exists() and preds_csv.exists() and cnn_csv.exists()):
        raise FileNotFoundError("Required data files are missing in data/.")

    grid_df = gpd.read_file(grid_gpkg)
    preds_df = pd.read_csv(preds_csv)
    cnn_df = pd.read_csv(cnn_csv)

    # Convert GPKG cell_id to grid_id format for merging
    def cell_id_to_grid_id(cell_id):
        try:
            parts = str(cell_id).split('_')
            if len(parts) == 3 and parts[0] == 'cell':
                x = int(parts[1])
                y = int(parts[2])
                return f"{x}_{y}"
        except:
            pass
        return None

    grid_df['grid_id'] = grid_df['cell_id'].apply(cell_id_to_grid_id)
    
    # Merge predictions with grid data
    merged = (
        grid_df.merge(preds_df, on="grid_id", how="inner")
        .dropna(subset=["pred_scaled_catboost", "pred_scaled_rf"])
        .reset_index(drop=True)
    )

    # Smooth cross-boundary prediction discontinuities for CatBoost / RF.
    # Border cells pick up spatial-aggregate features from neighbouring
    # barangays, biasing their predictions.  A gentle 30 % blend toward
    # the 4-connected neighbour mean reduces these artefacts.
    merged = _smooth_boundary_predictions(
        merged,
        pred_cols=["pred_scaled_catboost", "pred_scaled_rf"],
        blend=0.3,
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

    # ── Attach CNN predictions ──────────────────────────────────────────
    # Strategy 1: use cnn_pred column already present in the merged
    #             predictions CSV (produced by merge_predictions.py).
    # Strategy 2: fall back to joining with the separate CNN CSV via
    #             grid index parsing (legacy path).
    _cnn_attached = False

    if "cnn_pred" in merged.columns and merged["cnn_pred"].notna().any():
        # cnn_pred is in 0-1 scale; convert to percentage
        merged["poverty_pct_cnn"] = merged["cnn_pred"] * 100.0
        _cnn_attached = True

    elif {"cell_id", "predicted_poverty"}.issubset(cnn_df.columns):
        # Parse CNN cell_id → grid_id and join
        def _cnn_cell_to_grid_id(cell: str):
            try:
                parts = str(cell).split("_")
                if len(parts) == 3 and parts[0] == "cell":
                    return f"{int(parts[1])}_{int(parts[2])}"
            except Exception:
                pass
            return None

        cnn_tmp = cnn_df.copy()
        cnn_tmp["grid_id"] = cnn_tmp["cell_id"].apply(_cnn_cell_to_grid_id)
        cnn_tmp = cnn_tmp.dropna(subset=["grid_id", "predicted_poverty"])

        merged = merged.merge(
            cnn_tmp[["grid_id", "predicted_poverty"]],
            on="grid_id",
            how="left",
        )
        merged["poverty_pct_cnn"] = merged["predicted_poverty"] * 100.0
        merged.drop(columns=["predicted_poverty"], errors="ignore", inplace=True)
        _cnn_attached = True

    if _cnn_attached and merged["poverty_pct_cnn"].notna().any():
        merged["poverty_quartile_cnn"] = pd.qcut(
            merged["poverty_pct_cnn"].dropna() / 100.0,
            q=4,
            labels=labels,
            duplicates="drop",
        ).reindex(merged.index)
    else:
        merged["poverty_pct_cnn"] = pd.NA
        merged["poverty_quartile_cnn"] = pd.NA
    for _, row in merged.iterrows():
        # Use the geometry from GPKG - convert to GeoJSON format
        from shapely.geometry import mapping
        geom = mapping(row.geometry)
        brgy = row.get("barangay_name_clean", "")

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


@app.route("/api/statistics/barangay/<name>")
def api_statistics_for_barangay(name: str) -> object:
    """Return per-barangay values for all admin-exposed statistics sheets.

    This uses the same sheet configuration as the Statistics tab custom_sheets
    wiring, but filters each exposed sheet down to the requested barangay
    based on its configured x_column and first y_column.
    """

    try:
        target = (name or "").strip()
        if not target:
            return jsonify({"success": False, "error": "Missing barangay name"}), 400

        cfg_all = _load_sheets_config()
        summary = _load_sheets_summary()

        results: list[dict] = []

        if not summary.empty and isinstance(cfg_all, dict):
            # Ensure we have a safe_name column for joins
            if "safe_name" not in summary.columns:
                summary["safe_name"] = summary.get("sheet_name", "").apply(
                    _slugify_sheet_name
                )

            norm_target = target.strip().upper()

            for safe_name, cfg_entry in cfg_all.items():
                if not isinstance(cfg_entry, dict):
                    continue
                if not cfg_entry.get("expose_in_statistics"):
                    continue

                x_column = (cfg_entry.get("x_column") or "").strip()
                y_columns = cfg_entry.get("y_columns") or []
                if not x_column or not y_columns:
                    continue
                y_column = str(y_columns[0])

                row_mask = summary["safe_name"].astype(str) == str(safe_name)
                if not row_mask.any():
                    continue

                row = summary.loc[row_mask].iloc[0]
                csv_rel = str(row.get("csv_path") or "").strip()
                csv_path = _resolve_csv_path(csv_rel)
                if csv_path is None:
                    continue

                try:
                    df = pd.read_csv(csv_path)
                except Exception:
                    continue

                if x_column not in df.columns or y_column not in df.columns:
                    continue

                x_series = df[x_column].astype(str)
                mask_valid = x_series.notna() & (x_series.str.strip().str.upper() != "TOTAL")
                df_work = df.loc[mask_valid].copy()
                if df_work.empty:
                    continue

                x_norm = df_work[x_column].astype(str).str.strip().str.upper()
                mask_brgy = x_norm == norm_target
                df_sel = df_work.loc[mask_brgy]
                if df_sel.empty:
                    continue

                y_series = pd.to_numeric(df_sel[y_column], errors="coerce")
                if y_series.dropna().empty:
                    value: float | None = None
                else:
                    # If multiple rows match, sum them for a single value
                    value = float(y_series.fillna(0.0).sum())

                results.append(
                    {
                        "safe_name": safe_name,
                        "sheet_name": str(row.get("sheet_name") or safe_name),
                        "x_column": x_column,
                        "y_column": y_column,
                        "barangay": target,
                        "value": value,
                    }
                )

        return jsonify({"success": True, "barangay": target, "sheets": results})
    except Exception as exc:  # pragma: no cover
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/sheets", methods=["GET"])
def api_sheets_list() -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    df = _load_sheets_summary()
    records = df.to_dict(orient="records") if not df.empty else []
    return jsonify({"success": True, "sheets": records})


@app.route("/api/sheets/<safe_name>", methods=["GET"])
def api_sheets_get(safe_name: str) -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    df_summary = _load_sheets_summary()
    if df_summary.empty or "safe_name" not in df_summary.columns:
        return jsonify({"success": False, "error": "Sheet not found"}), 404

    row = df_summary.loc[df_summary["safe_name"] == safe_name]
    if row.empty:
        return jsonify({"success": False, "error": "Sheet not found"}), 404

    row_dict = row.iloc[0].to_dict()
    csv_rel = str(row_dict.get("csv_path") or "").strip()
    csv_path = _resolve_csv_path(csv_rel)
    
    if csv_path is None:
        return jsonify({"success": False, "error": "CSV file not found"}), 404

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to read CSV: {exc}"}), 500

    # Replace pandas NaN/NaT with None so that JSON serialization does not
    # emit invalid NaN literals, which would cause JSON.parse to fail
    # on the frontend when calling response.json().
    if not df.empty:
        df = df.where(pd.notnull(df), None)

    header = list(df.columns)
    rows = df.to_dict(orient="records")

    return jsonify(
        {
            "success": True,
            "sheet": row_dict,
            "header": header,
            "rows": rows,
        }
    )


@app.route("/api/sheets", methods=["POST"])
def api_sheets_create() -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    sheet_name = (payload.get("sheet_name") or "").strip()
    header = payload.get("header") or []
    rows = payload.get("rows") or []

    if not sheet_name:
        return jsonify({"success": False, "error": "sheet_name is required"}), 400

    if not isinstance(header, list) or not all(isinstance(c, str) for c in header):
        return jsonify({"success": False, "error": "header must be a list of column names"}), 400

    if not isinstance(rows, list):
        return jsonify({"success": False, "error": "rows must be a list of objects"}), 400

    safe_name = _slugify_sheet_name(sheet_name)

    df_summary = _load_sheets_summary()
    if not df_summary.empty and "safe_name" in df_summary.columns:
        if (df_summary["safe_name"] == safe_name).any():
            return jsonify({"success": False, "error": "A sheet with a similar name already exists"}), 400

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    csv_filename = f"{safe_name}.csv"
    csv_rel = f"csv_outputs/{csv_filename}"
    csv_path = CSV_DIR / csv_filename

    try:
        df = pd.DataFrame(rows, columns=header)
        df.to_csv(csv_path, index=False)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to write CSV: {exc}"}), 500

    rows_count = int(len(df))
    cols_count = int(len(header))

    if df_summary.empty:
        df_summary = pd.DataFrame(
            columns=["sheet_name", "safe_name", "rows", "columns", "csv_path"]
        )

    new_row = {
        "sheet_name": sheet_name,
        "safe_name": safe_name,
        "rows": rows_count,
        "columns": cols_count,
        "csv_path": csv_rel,
    }
    df_summary = pd.concat([df_summary, pd.DataFrame([new_row])], ignore_index=True)
    _save_sheets_summary(df_summary)

    return jsonify({"success": True, "sheet": new_row}), 201


@app.route("/api/sheets/upload", methods=["POST"])
def api_sheets_upload() -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    sheet_name = (request.form.get("sheet_name") or "").strip()
    if not sheet_name:
        # Derive a friendly name from the filename if not provided
        base = Path(file.filename).name
        sheet_name = base.rsplit(".", 1)[0] or base

    safe_name = _slugify_sheet_name(sheet_name)

    df_summary = _load_sheets_summary()
    if not df_summary.empty and "safe_name" in df_summary.columns:
        if (df_summary["safe_name"] == safe_name).any():
            return (
                jsonify({"success": False, "error": "A sheet with a similar name already exists"}),
                400,
            )

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    csv_filename = f"{safe_name}.csv"
    csv_rel = f"csv_outputs/{csv_filename}"
    csv_path = CSV_DIR / csv_filename

    try:
        file.save(csv_path)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to save uploaded file: {exc}"}), 500

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to read uploaded CSV: {exc}"}), 400

    rows_count = int(len(df))
    cols_count = int(len(df.columns))

    if df_summary.empty:
        df_summary = pd.DataFrame(
            columns=["sheet_name", "safe_name", "rows", "columns", "csv_path"]
        )

    new_row = {
        "sheet_name": sheet_name,
        "safe_name": safe_name,
        "rows": rows_count,
        "columns": cols_count,
        "csv_path": csv_rel,
    }
    df_summary = pd.concat([df_summary, pd.DataFrame([new_row])], ignore_index=True)
    _save_sheets_summary(df_summary)

    return jsonify({"success": True, "sheet": new_row}), 201


@app.route("/api/sheets/<safe_name>", methods=["PUT"])
def api_sheets_update(safe_name: str) -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    header = payload.get("header") or []
    rows = payload.get("rows") or []

    if not isinstance(header, list) or not all(isinstance(c, str) for c in header):
        return jsonify({"success": False, "error": "header must be a list of column names"}), 400

    if not isinstance(rows, list):
        return jsonify({"success": False, "error": "rows must be a list of objects"}), 400

    df_summary = _load_sheets_summary()
    if df_summary.empty or "safe_name" not in df_summary.columns:
        return jsonify({"success": False, "error": "Sheet not found"}), 404

    mask = df_summary["safe_name"] == safe_name
    if not mask.any():
        return jsonify({"success": False, "error": "Sheet not found"}), 404

    row = df_summary.loc[mask].iloc[0]
    csv_rel = str(row.get("csv_path") or "").strip()
    
    # For writing, resolve existing path or create new one
    csv_path = _resolve_csv_path(csv_rel)
    if csv_path is None:
        # Create new file in CSV_DIR
        csv_rel_normalized = csv_rel.replace("\\", "/")
        csv_filename = Path(csv_rel_normalized).name
        csv_path = CSV_DIR / csv_filename

    try:
        df = pd.DataFrame(rows, columns=header)
        CSV_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_path, index=False)
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to write CSV: {exc}"}), 500

    df_summary.loc[mask, "rows"] = int(len(df))
    df_summary.loc[mask, "columns"] = int(len(header))
    _save_sheets_summary(df_summary)

    return jsonify({"success": True})


@app.route("/api/sheets/<safe_name>", methods=["DELETE"])
def api_sheets_delete(safe_name: str) -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    df_summary = _load_sheets_summary()
    if df_summary.empty or "safe_name" not in df_summary.columns:
        return jsonify({"success": False, "error": "Sheet not found"}), 404

    mask = df_summary["safe_name"] == safe_name
    if not mask.any():
        return jsonify({"success": False, "error": "Sheet not found"}), 404

    row = df_summary.loc[mask].iloc[0]
    csv_rel = str(row.get("csv_path") or "").strip()
    csv_path = _resolve_csv_path(csv_rel)

    if csv_path is not None and csv_path.exists():
        try:
            csv_path.unlink()
        except Exception:
            # Non-fatal: we can still remove from summary
            pass

    df_summary = df_summary.loc[~mask].copy()
    if not df_summary.empty:
        df_summary = df_summary.reset_index(drop=True)
    _save_sheets_summary(df_summary)

    cfg = _load_sheets_config()
    if safe_name in cfg:
        cfg.pop(safe_name, None)
        _save_sheets_config(cfg)

    return jsonify({"success": True})


@app.route("/api/sheets/<safe_name>/config", methods=["GET"])
def api_sheets_get_config(safe_name: str) -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    cfg = _load_sheets_config()
    entry = cfg.get(safe_name) or {}
    return jsonify({"success": True, "config": entry})


@app.route("/api/sheets/<safe_name>/config", methods=["PUT"])
def api_sheets_update_config(safe_name: str) -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}
    chart_type = (payload.get("chart_type") or "bar").strip().lower()
    x_column = (payload.get("x_column") or "").strip()
    y_columns = payload.get("y_columns") or []

    if not isinstance(y_columns, list):
        return jsonify({"success": False, "error": "y_columns must be a list"}), 400

    # Optional extra configuration used by quick visualization and statistics wiring
    row_mode_raw = (payload.get("row_mode") or "").strip().lower()
    row_mode = row_mode_raw if row_mode_raw in {"top5", "all", "single"} else None
    sort_column = (payload.get("sort_column") or "").strip() or None
    filter_value = (payload.get("filter_value") or "").strip() or None
    filter_mode = (payload.get("filter_mode") or "").strip() or None

    expose_flag_present = "expose_in_statistics" in payload
    expose_in_statistics = bool(payload.get("expose_in_statistics")) if expose_flag_present else None

    allowed_types = {"bar", "line", "pie"}
    if chart_type not in allowed_types:
        chart_type = "bar"

    cfg = _load_sheets_config()

    if not x_column or not y_columns:
        # Clear config for this sheet if no meaningful selection
        if safe_name in cfg:
            cfg.pop(safe_name, None)
            _save_sheets_config(cfg)
        return jsonify({"success": True, "config": {}})

    entry: dict[str, object] = {
        "chart_type": chart_type,
        "x_column": x_column,
        "y_columns": [str(col) for col in y_columns],
    }

    if row_mode is not None:
        entry["row_mode"] = row_mode
    if sort_column is not None:
        entry["sort_column"] = sort_column
    if filter_value is not None:
        entry["filter_value"] = filter_value
    if filter_mode is not None:
        entry["filter_mode"] = filter_mode

    prev = cfg.get(safe_name) or {}
    if expose_flag_present:
        entry["expose_in_statistics"] = expose_in_statistics
    elif isinstance(prev, dict) and "expose_in_statistics" in prev:
        entry["expose_in_statistics"] = bool(prev.get("expose_in_statistics"))

    cfg[safe_name] = entry
    _save_sheets_config(cfg)

    return jsonify({"success": True, "config": cfg[safe_name]})


@app.route("/api/feedback", methods=["POST"])
def api_feedback_create() -> object:
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    message = (payload.get("message") or "").strip()
    barangay = (payload.get("barangay") or "").strip() or None

    if not _is_valid_email(email):
        return (
            jsonify({"success": False, "error": "Please provide a valid email address."}),
            400,
        )

    if not message:
        return (
            jsonify({"success": False, "error": "Message is required."}),
            400,
        )

    if len(message) > 4000:
        message = message[:4000]

    conn = _get_db_connection()
    try:
        cur = conn.execute(
            "SELECT id FROM public_messages WHERE email = ?",
            (email,),
        )
        if cur.fetchone() is not None:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "This email has already submitted a message.",
                    }
                ),
                400,
            )

        conn.execute(
            """
            INSERT INTO public_messages (email, barangay, message)
            VALUES (?, ?, ?)
            """,
            (email, barangay, message),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"success": True}), 201


@app.route("/api/feedback", methods=["GET"])
def api_feedback_list() -> object:
    if _get_current_user() is None:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    conn = _get_db_connection()
    try:
        cur = conn.execute(
            """
            SELECT email, barangay, message, created_at
            FROM public_messages
            ORDER BY created_at DESC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    return jsonify({"success": True, "messages": rows})


# ============================================================================
# REFRESH PIPELINE ENDPOINTS
# ============================================================================

def _get_last_refresh() -> dict | None:
    """Get the most recent successful refresh record."""
    conn = _get_db_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, started_at, completed_at, start_date, end_date, triggered_by, elapsed_seconds
            FROM refresh_history
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()
    return None


def _create_predictions_backup() -> str | None:
    """Create a backup of current prediction files before refresh."""
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"predictions_backup_{timestamp}"
    backup_path.mkdir(exist_ok=True)
    
    import shutil
    files_to_backup = [
        MERGED_PREDICTIONS_PATH,
        GRID_GEOJSON_PATH,
        CNN_PRED_PATH,
    ]
    
    backed_up = False
    for f in files_to_backup:
        if f.exists():
            shutil.copy2(f, backup_path / f.name)
            backed_up = True
    
    return str(backup_path) if backed_up else None


def _get_user_preferences(username: str) -> dict:
    """Get user preferences including refresh warning suppression."""
    conn = _get_db_connection()
    try:
        cur = conn.execute(
            "SELECT suppress_refresh_warning FROM user_preferences WHERE username = ?",
            (username,)
        )
        row = cur.fetchone()
        if row:
            return {"suppress_refresh_warning": bool(row["suppress_refresh_warning"])}
    finally:
        conn.close()
    return {"suppress_refresh_warning": False}


def _set_suppress_refresh_warning(username: str, suppress: bool) -> None:
    """Set user preference to suppress refresh warning."""
    conn = _get_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO user_preferences (username, suppress_refresh_warning, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                suppress_refresh_warning = excluded.suppress_refresh_warning,
                updated_at = CURRENT_TIMESTAMP
            """,
            (username, 1 if suppress else 0)
        )
        conn.commit()
    finally:
        conn.close()


@app.route("/api/refresh/check", methods=["GET"])
def api_refresh_check() -> object:
    """Check if refresh should warn about recent refresh."""
    user = _get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    prefs = _get_user_preferences(user["username"])
    last_refresh = _get_last_refresh()
    
    should_warn = False
    days_since_refresh = None
    
    if last_refresh and not prefs.get("suppress_refresh_warning"):
        completed_at = datetime.fromisoformat(last_refresh["completed_at"].replace("Z", "+00:00"))
        days_since = (datetime.now() - completed_at.replace(tzinfo=None)).days
        if days_since < REFRESH_COOLDOWN_DAYS:
            should_warn = True
            days_since_refresh = days_since
    
    return jsonify({
        "success": True,
        "should_warn": should_warn,
        "days_since_refresh": days_since_refresh,
        "cooldown_days": REFRESH_COOLDOWN_DAYS,
        "last_refresh": last_refresh,
        "suppress_warning": prefs.get("suppress_refresh_warning", False),
    })


@app.route("/api/refresh/suppress-warning", methods=["POST"])
def api_refresh_suppress_warning() -> object:
    """Suppress the refresh warning for the current user."""
    user = _get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    payload = request.get_json(silent=True) or {}
    suppress = payload.get("suppress", True)
    
    _set_suppress_refresh_warning(user["username"], suppress)
    
    return jsonify({"success": True, "suppress_warning": suppress})


@app.route("/api/refresh", methods=["POST"])
def api_refresh() -> object:
    """Trigger a refresh of prediction layers from upstream data sources.

    This endpoint:
    1. Validates admin authentication
    2. Validates date range parameters
    3. Creates backup of current predictions
    4. Starts the refresh pipeline in background
    5. Records refresh in history table
    
    Request JSON:
    {
        "start_date": "YYYY-MM-DD",  // Optional, defaults to 1 year ago
        "end_date": "YYYY-MM-DD",    // Optional, defaults to today
        "skip_gee": false,           // Optional, skip GEE extraction
        "force": false               // Optional, bypass cooldown warning
    }
    """
    # Check authentication
    user = _get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized. Admin login required."}), 401
    
    # Parse request
    payload = request.get_json(silent=True) or {}
    
    # Validate and parse dates
    end_date_str = payload.get("end_date")
    start_date_str = payload.get("start_date")
    skip_gee = payload.get("skip_gee", False)
    force = payload.get("force", False)
    
    # Default end date is today
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"success": False, "error": "Invalid end_date format. Use YYYY-MM-DD."}), 400
    else:
        end_date = datetime.now()
        end_date_str = end_date.strftime("%Y-%m-%d")
    
    # Default start date is 1 year before end date
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"success": False, "error": "Invalid start_date format. Use YYYY-MM-DD."}), 400
    else:
        start_date = end_date - timedelta(days=365)
        start_date_str = start_date.strftime("%Y-%m-%d")
    
    # Validate date range
    if start_date >= end_date:
        return jsonify({"success": False, "error": "start_date must be before end_date."}), 400
    
    date_range_days = (end_date - start_date).days
    if date_range_days > MAX_DATE_RANGE_DAYS:
        return jsonify({
            "success": False, 
            "error": f"Date range cannot exceed {MAX_DATE_RANGE_DAYS} days. Requested: {date_range_days} days."
        }), 400
    
    if end_date > datetime.now():
        return jsonify({"success": False, "error": "end_date cannot be in the future."}), 400
    
    # Check if refresh is already running
    try:
        from src.workflow.refresh_pipeline import is_refresh_running, run_refresh_async, get_status
        
        if is_refresh_running():
            status = get_status()
            return jsonify({
                "success": False,
                "error": "A refresh is already in progress.",
                "current_status": status
            }), 409
    except ImportError as e:
        return jsonify({"success": False, "error": f"Refresh module not available: {e}"}), 500
    
    # Check cooldown (unless forced)
    if not force:
        last_refresh = _get_last_refresh()
        if last_refresh:
            completed_at = datetime.fromisoformat(last_refresh["completed_at"].replace("Z", "+00:00"))
            days_since = (datetime.now() - completed_at.replace(tzinfo=None)).days
            if days_since < REFRESH_COOLDOWN_DAYS:
                prefs = _get_user_preferences(user["username"])
                if not prefs.get("suppress_refresh_warning"):
                    return jsonify({
                        "success": False,
                        "error": "cooldown_warning",
                        "days_since_refresh": days_since,
                        "cooldown_days": REFRESH_COOLDOWN_DAYS,
                        "message": f"Last refresh was {days_since} days ago. Use force=true to proceed."
                    }), 429
    
    # Create backup before refresh
    backup_path = _create_predictions_backup()
    
    # Record refresh start in database
    conn = _get_db_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO refresh_history (status, start_date, end_date, triggered_by, predictions_backup_path)
            VALUES ('running', ?, ?, ?, ?)
            """,
            (start_date_str, end_date_str, user["username"], backup_path)
        )
        refresh_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    
    # Start the refresh pipeline
    try:
        thread = run_refresh_async(
            project_root=str(ROOT),
            start_date=start_date_str,
            end_date=end_date_str,
            skip_gee=skip_gee,
        )
        
        if thread is None:
            return jsonify({
                "success": False, 
                "error": "Failed to start refresh - another refresh may be running."
            }), 409
        
        # Start a thread to monitor completion and update database
        def _monitor_completion():
            thread.join()
            status = get_status()
            conn = _get_db_connection()
            try:
                if status.get("phase") == "COMPLETED":
                    conn.execute(
                        """
                        UPDATE refresh_history 
                        SET status = 'completed', completed_at = CURRENT_TIMESTAMP, elapsed_seconds = ?
                        WHERE id = ?
                        """,
                        (status.get("elapsed_seconds", 0), refresh_id)
                    )
                else:
                    conn.execute(
                        """
                        UPDATE refresh_history 
                        SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
                        WHERE id = ?
                        """,
                        (status.get("error", "Unknown error"), refresh_id)
                    )
                conn.commit()
            finally:
                conn.close()
        
        monitor_thread = threading.Thread(target=_monitor_completion, daemon=True)
        monitor_thread.start()
        
        return jsonify({
            "success": True,
            "message": f"Refresh started for {start_date_str} to {end_date_str}",
            "refresh_id": refresh_id,
            "backup_path": backup_path,
        })
        
    except Exception as exc:
        # Update database with failure
        conn = _get_db_connection()
        try:
            conn.execute(
                """
                UPDATE refresh_history 
                SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ?
                WHERE id = ?
                """,
                (str(exc), refresh_id)
            )
            conn.commit()
        finally:
            conn.close()
        
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/refresh/status", methods=["GET"])
def api_refresh_status() -> object:
    """Get the current status of an ongoing refresh."""
    try:
        from src.workflow.refresh_pipeline import get_status, is_refresh_running
        
        status = get_status()
        status["is_running"] = is_refresh_running()
        
        return jsonify({"success": True, **status})
    except Exception:
        # Always return valid JSON even on error
        return jsonify({
            "success": True,
            "phase": "IDLE",
            "message": "No refresh in progress",
            "progress": 0,
            "is_running": False
        })


@app.route("/api/refresh/cancel", methods=["POST"])
def api_refresh_cancel() -> object:
    """Cancel an ongoing refresh."""
    user = _get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    try:
        from src.workflow.refresh_pipeline import cancel_refresh, is_refresh_running
        
        if not is_refresh_running():
            return jsonify({"success": False, "error": "No refresh in progress"})
        
        cancel_refresh()
        return jsonify({"success": True, "message": "Refresh cancelled"})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/refresh/history", methods=["GET"])
def api_refresh_history() -> object:
    """Get refresh history."""
    user = _get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    limit = request.args.get("limit", 10, type=int)
    
    conn = _get_db_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, started_at, completed_at, status, start_date, end_date, 
                   triggered_by, error_message, elapsed_seconds
            FROM refresh_history
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    
    return jsonify({"success": True, "history": rows})


@app.route("/api/refresh/rollback/<int:refresh_id>", methods=["POST"])
def api_refresh_rollback(refresh_id: int) -> object:
    """Rollback to predictions from before a specific refresh."""
    user = _get_current_user()
    if not user:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    conn = _get_db_connection()
    try:
        cur = conn.execute(
            "SELECT predictions_backup_path FROM refresh_history WHERE id = ?",
            (refresh_id,)
        )
        row = cur.fetchone()
    finally:
        conn.close()
    
    if not row:
        return jsonify({"success": False, "error": "Refresh record not found."}), 404
    
    backup_path = row["predictions_backup_path"]
    if not backup_path:
        return jsonify({"success": False, "error": "No backup available for this refresh."}), 404
    
    backup_dir = Path(backup_path)
    if not backup_dir.exists():
        return jsonify({"success": False, "error": "Backup directory not found."}), 404
    
    import shutil
    
    restored = []
    try:
        # Restore each backed up file
        for backup_file in backup_dir.iterdir():
            target = DATA_DIR / backup_file.name
            shutil.copy2(backup_file, target)
            restored.append(str(target))
        
        return jsonify({
            "success": True,
            "message": f"Rolled back to backup from refresh #{refresh_id}",
            "restored_files": restored
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Rollback failed: {e}"}), 500


@app.route("/health", methods=["GET"])
def health_check() -> object:
    """Health check endpoint for deployment monitoring."""
    try:
        # Check database connectivity
        conn = _get_db_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        
        # Check required files exist
        required_files = [
            DATA_DIR / "users.db",
            SHAPEFILE_PATH,
        ]
        missing_files = [f for f in required_files if not f.exists()]
        
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0",
            "database": "connected",
            "required_files": "present" if not missing_files else f"missing: {missing_files}"
        }
        
        return jsonify(status), 200
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }), 500


if __name__ == "__main__":  # pragma: no cover
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
