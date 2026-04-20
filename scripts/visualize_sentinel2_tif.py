#!/usr/bin/env python3

import argparse
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image


def build_rgb_from_s2(tif_path: Path, r: int = 3, g: int = 2, b: int = 1, gamma: float = 1.0) -> Image.Image:
    """Load a Sentinel-2 GeoTIFF and return an RGB PIL.Image.

    Band indices are 1-based as in rasterio.
    Defaults assume band order [B2, B3, B4, B8, B11, B12], so R=3 (B4), G=2 (B3), B=1 (B2).
    """
    with rasterio.open(tif_path) as src:
        # Read selected bands (C, H, W) and convert to float32
        arr = src.read([r, g, b]).astype(np.float32)

    # (H, W, C)
    rgb = np.transpose(arr, (1, 2, 0))

    # Percentile stretch per channel
    out = np.zeros_like(rgb, dtype=np.float32)
    for i in range(3):
        band = rgb[:, :, i]
        mask = band > 0
        if not np.any(mask):
            continue
        p2, p98 = np.percentile(band[mask], (2, 98))
        if p98 <= p2:
            p98 = p2 + 1e-6
        scaled = (band - p2) / (p98 - p2)
        scaled = np.clip(scaled, 0.0, 1.0)
        if gamma != 1.0:
            scaled = np.power(scaled, 1.0 / gamma)
        out[:, :, i] = scaled

    rgb_u8 = (out * 255.0).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgb_u8, mode="RGB")


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize Sentinel-2 GeoTIFF as RGB PNG")
    parser.add_argument(
        "--input",
        type=str,
        default="data/satellite_imagery/sentinel2_zamboanga_2024_improved.tif",
        help="Path to Sentinel-2 GeoTIFF",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output/visualizations/sentinel2_zamboanga_2024_rgb.png",
        help="Output PNG path",
    )
    parser.add_argument(
        "--r",
        type=int,
        default=3,
        help="1-based band index for red channel (default: 3 = B4)",
    )
    parser.add_argument(
        "--g",
        type=int,
        default=2,
        help="1-based band index for green channel (default: 2 = B3)",
    )
    parser.add_argument(
        "--b",
        type=int,
        default=1,
        help="1-based band index for blue channel (default: 1 = B2)",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=1.0,
        help="Gamma correction (>1 brightens mid-tones; default: 1.0)",
    )

    args = parser.parse_args()

    tif_path = Path(args.input)
    if not tif_path.exists():
        raise FileNotFoundError(f"Input GeoTIFF not found: {tif_path}")

    img = build_rgb_from_s2(tif_path, r=args.r, g=args.g, b=args.b, gamma=args.gamma)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    print(f"Saved PNG to {out_path}")


if __name__ == "__main__":
    main()
