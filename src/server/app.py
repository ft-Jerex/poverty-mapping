from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
from flask import Flask, jsonify, send_from_directory

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


@app.route("/")
def index() -> object:  # pragma: no cover
    return send_from_directory(app.static_folder, "index.html")


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

    # Boundary from min/max lon/lat
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
    """Serve real per-cell predictions to the Leaflet frontend.

    This endpoint reads CSVs under data/ and returns a JSON payload of the form
    expected by static/app.js.
    """
    try:
        boundary, labels, models = _load_grid_predictions()
        return jsonify(
            {
                "boundary": boundary,
                "barangayLabels": labels,
                "models": models,
            }
        )
    except Exception as exc:  # pragma: no cover - logged via JSON
        # Frontend will show an error status if error is present.
        return jsonify(
            {
                "boundary": None,
                "barangayLabels": None,
                "models": {"catboost": None, "rf": None, "cnn": None},
                "error": str(exc),
            }
        )


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
