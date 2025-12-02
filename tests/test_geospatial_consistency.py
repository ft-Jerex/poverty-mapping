from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from src.workflow.predict_layers import run_prediction


def test_prediction_layer_geospatial_consistency(tmp_path: Path):
    # Create a tiny 8x8 3-band feature raster
    width = height = 8
    transform = from_origin(120.0, 15.0, 0.01, 0.01)

    band1 = np.random.rand(height, width).astype("float32")
    band2 = np.random.rand(height, width).astype("float32")
    band3 = np.random.rand(height, width).astype("float32")
    data = np.stack([band1, band2, band3])

    features_path = tmp_path / "features.tif"
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
    }
    with rasterio.open(features_path, "w", **profile) as dst:
        dst.write(data)

    output_path = tmp_path / "predictions.tif"
    run_prediction(features_path, output_path)

    assert output_path.exists()

    with rasterio.open(features_path) as src_f, rasterio.open(output_path) as src_p:
        assert src_p.width == src_f.width
        assert src_p.height == src_f.height
        assert src_p.crs == src_f.crs
        assert src_p.transform == src_f.transform
        assert src_p.count == 1
