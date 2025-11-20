"""Local workflow: load features, run poverty model stub, write prediction layer."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import Affine

from src.model.stub_model import load_model


def run_prediction(input_path: Path, output_path: Path) -> None:
    with rasterio.open(input_path) as src:
        data = src.read()  # [bands, H, W]
        profile = src.profile

    # Move channels to last dimension: [H, W, C]
    features = np.moveaxis(data, 0, -1)
    num_features = features.shape[-1]
    model = load_model(num_features)

    scores = model.predict(features)  # [H, W]

    out_profile = profile.copy()
    out_profile.update(count=1, dtype="float32")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, "w", **out_profile) as dst:
        dst.write(scores.astype("float32"), 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run poverty model stub on a feature raster.")
    parser.add_argument("--input", type=str, required=True, help="Input feature GeoTIFF path")
    parser.add_argument("--output", type=str, required=True, help="Output prediction GeoTIFF path")
    args = parser.parse_args()

    run_prediction(Path(args.input), Path(args.output))


if __name__ == "__main__":  # pragma: no cover
    main()
