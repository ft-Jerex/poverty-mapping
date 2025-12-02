"""Demonstration script: synthetic features -> model stub -> prediction layer."""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.workflow.predict_layers import run_prediction


def create_synthetic_features(path: Path) -> None:
    """Create a tiny synthetic 3-band feature raster for demo/testing."""
    width = height = 16
    transform = from_origin(120.0, 15.0, 0.01, 0.01)  # arbitrary but fixed

    band1 = np.linspace(0, 1, width * height).reshape((height, width))  # NDVI-like
    band2 = np.full((height, width), 0.5, dtype="float32")  # rainfall-like
    band3 = np.linspace(0, 1000, width * height).reshape((height, width))  # elevation-like

    data = np.stack([band1, band2, band3]).astype("float32")

    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "demo_data"
    features_path = data_dir / "synthetic_features.tif"
    output_path = data_dir / "synthetic_poverty_map.tif"

    create_synthetic_features(features_path)
    run_prediction(features_path, output_path)

    print(f"Synthetic features written to: {features_path}")
    print(f"Synthetic prediction layer written to: {output_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
