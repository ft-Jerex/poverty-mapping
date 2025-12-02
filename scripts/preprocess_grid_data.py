import json
import os
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
import re


# Base paths - script lives in scripts/ but data is in project root
BASE_DIR = Path(__file__).resolve().parent  # scripts/
PROJECT_ROOT = BASE_DIR.parent  # project root
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"  # assets/ at project root
SHAPE_DIR = ASSETS_DIR / "shapefile"
EXPORTS_DIR = PROJECT_ROOT / "googleEarthExports"
STATUS_FILE = PROJECT_ROOT / "preprocessing_status.json"
SOCIO_DIR = ASSETS_DIR / "socioeconomic_csv_original"
SOCIO_MAIN_DIR = ASSETS_DIR / "socioeconomic_csv"


def update_status(phase: str, message: str = "", extra: Optional[dict] = None) -> None:
    """Write a small JSON status file for the web frontend to poll."""
    data = {"phase": phase, "message": message}
    if extra:
        data.update(extra)
    try:
        STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        # Best-effort only; never crash on status write
        pass


def load_grid_export() -> pd.DataFrame:
    """Load the raw grid export from geospatial_prep.
    
    This now contains MULTIPLE SAMPLES per grid cell from sampleRegions.
    Each sample has x_idx, y_idx (or grid_id) to identify its parent grid cell.
    
    Expected input (created by geospatial_prep.py):
      googleEarthExports/zc04_grid_data_2024.csv
    """
    update_status("LOADING_GRID", "Loading raw grid export from geospatial_prep")

    csv_path = EXPORTS_DIR / "zc04_grid_data_2024.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Raw grid export not found at {csv_path}. "
            "Run geospatial_prep.py successfully before preprocessing.",
        )

    df_grid = pd.read_csv(csv_path)
    
    # Check for grid identifiers
    if "grid_id" not in df_grid.columns and not {"x_idx", "y_idx"}.issubset(df_grid.columns):
        raise KeyError(
            "Input grid CSV is missing required grid identifier columns. "
            "Expected 'grid_id' or both 'x_idx' and 'y_idx'.",
        )
    
    # Create grid_cell_id if not present
    if "grid_id" not in df_grid.columns:
        df_grid["grid_id"] = df_grid["x_idx"].astype(str) + "_" + df_grid["y_idx"].astype(str)
    
    # Parse geometry from .geo column if present
    if ".geo" in df_grid.columns:
        try:
            from shapely import wkt
            from shapely.geometry import shape
            
            def parse_geometry(geom_str):
                if pd.isna(geom_str):
                    return None
                try:
                    # Try GeoJSON format first
                    if isinstance(geom_str, str) and geom_str.strip().startswith("{"):
                        return shape(json.loads(geom_str))
                    # Fallback to WKT
                    return wkt.loads(geom_str)
                except Exception:
                    return None
            
            df_grid["geometry"] = df_grid[".geo"].apply(parse_geometry)
            df_grid = df_grid.drop(columns=[".geo"])
        except Exception as e:
            update_status("LOADING_GRID", f"Warning: Could not parse .geo column: {e}")
    
    unique_grids = df_grid["grid_id"].nunique()
    total_samples = len(df_grid)
    avg_samples = total_samples / unique_grids if unique_grids > 0 else 0
    
    update_status(
        "GRID_LOADED",
        f"Loaded grid export: {total_samples} samples across {unique_grids} grid cells (avg {avg_samples:.1f} samples/cell)",
        {
            "total_samples": int(total_samples),
            "unique_grids": int(unique_grids),
            "avg_samples_per_grid": float(avg_samples),
        },
    )
    
    return df_grid


def normalize_barangay_name(name: str) -> Optional[str]:
    if pd.isna(name):
        return None
    text = str(name).upper().strip()
    # Remove content in parentheses
    text = re.sub(r"\(.*?\)", "", text)
    # Remove common tokens/prefixes
    for token in ["BARANGAY", "BRGY.", "BRGY", "BAR."]:
        text = text.replace(token, "")
    # Normalize punctuation/hyphens and collapses
    text = text.replace("-", " ")
    text = " ".join(text.split())
    return text


def build_barangay_df_from_folder() -> pd.DataFrame:
    candidates = [
        SOCIO_MAIN_DIR / "Number of Poor Households.clean.csv",
        SOCIO_MAIN_DIR / "Number of Poor Individuals.clean.csv",
        SOCIO_MAIN_DIR / "Number of Poor Households.csv",
        SOCIO_MAIN_DIR / "Number of Poor Individuals.csv",
    ]
    csv_path = None
    for path in candidates:
        if path.exists():
            csv_path = path
            break

    if csv_path is None:
        raise FileNotFoundError(
            "Could not assemble barangay socioeconomic table from assets/socioeconomic_csv/.",
        )

    df_parts = pd.read_csv(csv_path)

    name_col = None
    for candidate in [
        "barangay_name_clean",
        "_orig_Barangays",
        "_orig_Barangay",
        "Barangay",
        "Barangays",
        "Barangay Name",
        "Barangay_Name",
    ]:
        if candidate in df_parts.columns:
            name_col = candidate
            break
    if name_col is None:
        raise KeyError("Could not determine barangay name column in socioeconomic CSV.")

    poverty_col = None
    for candidate in df_parts.columns:
        cand_lower = candidate.lower()
        if "poverty" in cand_lower and "magnitude" in cand_lower:
            poverty_col = candidate
            break
    if poverty_col is None:
        for candidate in df_parts.columns:
            if "poverty" in candidate.lower():
                poverty_col = candidate
                break
    if poverty_col is None:
        raise KeyError("Could not determine poverty column (e.g., 'Poverty Magnitude').")

    pop_col = None
    for candidate in df_parts.columns:
        cand_lower = candidate.lower()
        if "total assessed" in cand_lower or "total household" in cand_lower:
            pop_col = candidate
            break
    if pop_col is None:
        for candidate in df_parts.columns:
            cand_lower = candidate.lower()
            if "identified poor" in cand_lower or "population" in cand_lower:
                pop_col = candidate
                break

    df_out = pd.DataFrame()
    df_out["barangay_name_clean"] = df_parts[name_col].apply(normalize_barangay_name)
    df_out["poverty_rate"] = pd.to_numeric(df_parts[poverty_col], errors="coerce")
    if pop_col:
        df_out["population"] = pd.to_numeric(df_parts[pop_col], errors="coerce")

    df_out = df_out.dropna(subset=["barangay_name_clean"]).copy()
    update_status(
        "SOCIO_LOADED",
        f"Reconstructed barangay data from {csv_path.name}",
        {"source": str(csv_path)},
    )
    return df_out


def load_barangay_data() -> pd.DataFrame:
    """Load barangay-level socioeconomic data.

    We expect a CSV with at least:
      - barangay_name_clean
      - poverty_rate
      - population  (or similar total population field)

    The file can be placed either directly under assets/ as
    'barangay_with_all_features.csv' or under
    assets/socioeconomic_csv/ with the same name.
    """
    update_status("LOADING_SOCIO", "Loading barangay-level socioeconomic data")

    candidates = [
        ASSETS_DIR / "barangay_with_all_features.csv",
        SOCIO_MAIN_DIR / "barangay_with_all_features.csv",
        SOCIO_DIR / "barangay_with_all_features.csv",  # fallback to old path
    ]

    csv_path = None
    for path in candidates:
        if path.exists():
            csv_path = path
            break

    if csv_path is not None:
        df = pd.read_csv(csv_path)
        # Normalize barangay names from CSV to match shapefile normalization
        if "barangay_name_clean" in df.columns:
            df["barangay_name_clean"] = df["barangay_name_clean"].apply(normalize_barangay_name)
    else:
        df = build_barangay_df_from_folder()

    required = {"barangay_name_clean", "poverty_rate"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(
            "Barangay CSV is missing required columns: " + ", ".join(sorted(missing)),
        )

    if "population" not in df.columns:
        # Not fatal, but warn via status
        update_status(
            "SOCIO_LOADED",
            "Barangay CSV has no 'population' column; population-weighted scaling "
            "will be disabled.",
        )
    else:
        update_status(
            "SOCIO_LOADED",
            "Loaded barangay data including poverty_rate and population.",
        )

    return df


def attach_barangay_to_samples(samples_df: pd.DataFrame, barangay_df: pd.DataFrame) -> pd.DataFrame:
    """Spatially join each sample to a barangay and attach poverty & population.
    
    This is adapted to handle MULTIPLE SAMPLES per grid cell.
    Each sample gets assigned to a barangay based on its geometry.

    Approach:
      - Load barangay polygons from zc04AdminBoundaries.shp.
      - Merge with barangay_df on 'barangay_name_clean'.
      - Spatial join sample geometries (or centroids if needed) to barangay polygons.
      - Compute barangay area in km² as shape_sqkm for weighting.
    """
    update_status("JOINING_BARANGAYS", "Attaching barangay attributes to samples")

    bnd_path = SHAPE_DIR / "zc04AdminBoundaries.shp"
    if not bnd_path.exists():
        raise FileNotFoundError(
            f"Barangay shapefile not found at {bnd_path}. "
            "Copy it into povmapbackend/assets/shapefile/.",
        )

    bnd_gdf = gpd.read_file(bnd_path)
    
    # Dissolve multiple features into single geometry
    if len(bnd_gdf) > 1:
        update_status("JOINING_BARANGAYS", f"Dissolving {len(bnd_gdf)} barangay features")
        # Check if we have a barangay name column to group by
        if "barangay_name_clean" in bnd_gdf.columns or "adm4_en" in bnd_gdf.columns:
            # Group by barangay name if available
            name_col = "barangay_name_clean" if "barangay_name_clean" in bnd_gdf.columns else "adm4_en"
            bnd_gdf = bnd_gdf.dissolve(by=name_col).reset_index()
        else:
            # Otherwise just dissolve all into one
            bnd_gdf = bnd_gdf.dissolve().reset_index()
    
    if bnd_gdf.crs is None:
        bnd_gdf.set_crs(epsg=4326, inplace=True)

    # Ensure we have a clean barangay name to join on
    if "barangay_name_clean" not in bnd_gdf.columns:
        if "adm4_en" in bnd_gdf.columns:
            bnd_gdf["barangay_name_clean"] = bnd_gdf["adm4_en"].astype(str)
        else:
            bnd_gdf["barangay_name_clean"] = bnd_gdf.index.astype(str)
    # Normalize shapefile names to align with socioeconomic CSV
    bnd_gdf["barangay_name_clean"] = bnd_gdf["barangay_name_clean"].apply(normalize_barangay_name)

    # Merge socioeconomic attributes
    barangay_df = barangay_df.copy()
    barangay_df["barangay_name_clean"] = barangay_df["barangay_name_clean"].astype(str)
    bnd_gdf["barangay_name_clean"] = bnd_gdf["barangay_name_clean"].astype(str)

    bnd_merged = bnd_gdf.merge(
        barangay_df,
        on="barangay_name_clean",
        how="left",
        suffixes=("", "_soc"),
    )

    # Prefer socioeconomic columns when present, since shapefile may have placeholder columns
    for col in ["poverty_rate", "population"]:
        soc_col = f"{col}_soc"
        if soc_col in bnd_merged.columns:
            base_vals = bnd_merged.get(col)
            soc_vals = bnd_merged[soc_col]
            bnd_merged[col] = soc_vals.combine_first(base_vals)

    # Drop the temporary '_soc' columns
    bnd_merged = bnd_merged.drop(columns=[c for c in bnd_merged.columns if c.endswith("_soc")], errors="ignore")

    # Fuzzy matching fallback for missing poverty_rate
    try:
        n_missing = int(bnd_merged["poverty_rate"].isna().sum())
    except Exception:
        n_missing = 0
    if n_missing:
        from difflib import SequenceMatcher
        socio_lookup = (
            barangay_df[["barangay_name_clean", "poverty_rate"] + (["population"] if "population" in barangay_df.columns else [])]
            .dropna(subset=["barangay_name_clean"])
        )
        socio_lookup["barangay_name_clean"] = socio_lookup["barangay_name_clean"].apply(normalize_barangay_name)
        name_list = socio_lookup["barangay_name_clean"].tolist()
        # Build dict for quick value retrieval
        if "population" in socio_lookup.columns:
            socio_map = socio_lookup.set_index("barangay_name_clean")["poverty_rate"].to_dict()
            pop_map = socio_lookup.set_index("barangay_name_clean")["population"].to_dict()
        else:
            socio_map = socio_lookup.set_index("barangay_name_clean")["poverty_rate"].to_dict()
            pop_map = {}

        def best_match(nm: str):
            best, br = None, 0.0
            for cand in name_list:
                r = SequenceMatcher(None, nm, cand).ratio()
                if r > br:
                    best, br = cand, r
            if best and br >= 0.85:
                return socio_map.get(best), pop_map.get(best, np.nan)
            return np.nan, np.nan

        miss_mask = bnd_merged["poverty_rate"].isna()
        if miss_mask.any():
            filled = bnd_merged.loc[miss_mask, "barangay_name_clean"].apply(lambda x: best_match(x) if isinstance(x, str) else (np.nan, np.nan))
            if len(filled) > 0:
                pr = [t[0] for t in filled]
                pp = [t[1] for t in filled]
                bnd_merged.loc[miss_mask, "poverty_rate"] = pr
                if "population" in bnd_merged.columns:
                    pop_series = pd.Series(pp, index=filled.index)
                    bnd_merged.loc[miss_mask, "population"] = bnd_merged.loc[miss_mask, "population"].combine_first(pop_series)

    # Compute barangay area in km²
    try:
        bnd_proj = bnd_merged.to_crs("EPSG:32651")  # UTM zone covering Zamboanga
        areas_m2 = bnd_proj.geometry.area
        shape_sqkm = areas_m2 / 1_000_000.0
        bnd_merged["shape_sqkm"] = shape_sqkm
    except Exception:
        bnd_merged["shape_sqkm"] = np.nan
        update_status(
            "JOINING_BARANGAYS",
            "Warning: could not compute accurate barangay areas; shape_sqkm is NaN.",
        )

    # Create GeoDataFrame from samples
    if "geometry" in samples_df.columns:
        # Samples already have geometry
        samples_gdf = gpd.GeoDataFrame(samples_df, geometry="geometry", crs="EPSG:4326")
        
        # Get centroids for spatial join
        samples_gdf["centroid"] = samples_gdf.geometry.centroid
        samples_for_join = samples_gdf.set_geometry("centroid")
    else:
        # No geometry column - this shouldn't happen with current geospatial_prep
        raise ValueError(
            "Samples dataframe has no 'geometry' column. "
            "Ensure geospatial_prep.py is using sampleRegions with geometries=True."
        )

    # Spatial join
    bnd_for_join = bnd_merged.to_crs("EPSG:4326")
    
    joined = gpd.sjoin(
        samples_for_join,
        bnd_for_join[["barangay_name_clean", "poverty_rate", "population", "shape_sqkm", "geometry"]],
        how="left",
        predicate="intersects",
    )

    # Clean up
    joined = joined.drop(columns=["index_right", "centroid"], errors="ignore")
    joined = joined.set_geometry("geometry")

    # Check for missing assignments
    missing_brg = joined["barangay_name_clean"].isna().sum()
    if missing_brg:
        update_status(
            "JOINING_BARANGAYS",
            f"Warning: {missing_brg} samples could not be assigned to a barangay.",
            {"unassigned_samples": int(missing_brg)},
        )

    # Add lon/lat from centroids if not present
    if "lon" not in joined.columns or "lat" not in joined.columns:
        centroids = joined.geometry.centroid
        joined["lon"] = centroids.x
        joined["lat"] = centroids.y

    update_status(
        "BARANGAYS_ATTACHED",
        "Attached barangay_name_clean, poverty_rate, population, and shape_sqkm to samples.",
    )

    return joined


def save_comprehensive_dataset(df: pd.DataFrame) -> Path:
    """Save the comprehensive sample-level dataset for modeling.

    The output contains MULTIPLE SAMPLES per grid cell, which will be
    handled by the adapted training scripts using sample weighting.
    """
    out_path = ASSETS_DIR / "grid_with_comprehensive_data.csv"
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # If file exists, rename it with its creation date/time
    if out_path.exists():
        import datetime
        stat = out_path.stat()
        ctime = getattr(stat, 'st_ctime', stat.st_mtime)
        dt = datetime.datetime.fromtimestamp(ctime)
        ts = dt.strftime("%Y%m%d_%H%M%S")
        base_backup = ASSETS_DIR / f"grid_with_comprehensive_data_{ts}.csv"
        backup_path = base_backup
        counter = 1
        while backup_path.exists():
            backup_path = ASSETS_DIR / f"grid_with_comprehensive_data_{ts}_{counter}.csv"
            counter += 1
        out_path.rename(backup_path)
        update_status(
            "PREPROCESS_BACKUP",
            f"Existing file renamed to {backup_path}",
            {"backup_path": str(backup_path)},
        )

    # Drop geometry column for CSV export (optional - keep if needed)
    if "geometry" in df.columns:
        df_to_save = df.copy()
        # Convert geometry to WKT for CSV storage
        df_to_save["geometry_wkt"] = df_to_save["geometry"].apply(lambda g: g.wkt if g else None)
        df_to_save = df_to_save.drop(columns=["geometry"])
    else:
        df_to_save = df

    df_to_save.to_csv(out_path, index=False)
    
    # Statistics
    unique_grids = df["grid_id"].nunique() if "grid_id" in df.columns else "unknown"
    total_samples = len(df)
    
    update_status(
        "PREPROCESS_DONE",
        f"Saved comprehensive dataset: {total_samples} samples across {unique_grids} grid cells",
        {
            "output_path": str(out_path),
            "total_samples": int(total_samples),
            "unique_grids": int(unique_grids) if isinstance(unique_grids, int) else unique_grids,
        },
    )
    return out_path


def main() -> int:
    update_status("STARTED", "Preprocessing grid export for modeling")

    try:
        samples_df = load_grid_export()
        barangay_df = load_barangay_data()
        full_df = attach_barangay_to_samples(samples_df, barangay_df)
        out_path = save_comprehensive_dataset(full_df)
        
        print(f"\n{'='*60}")
        print(f"Preprocessing completed successfully!")
        print(f"{'='*60}")
        print(f"Output: {out_path}")
        print(f"Total samples: {len(full_df)}")
        if "grid_id" in full_df.columns:
            unique_grids = full_df["grid_id"].nunique()
            print(f"Unique grid cells: {unique_grids}")
            print(f"Average samples per grid: {len(full_df)/unique_grids:.1f}")
        print(f"{'='*60}")
        
        update_status("DONE", "Preprocessing completed successfully")
        return 0
    except Exception as e:
        msg = f"Preprocessing failed: {e}"
        print(msg)
        import traceback
        traceback.print_exc()
        update_status("ERROR", msg)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())