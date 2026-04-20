"""Create a single image comparing CatBoost, RF, and CNN grid predictions."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREDICTIONS_CSV = PROJECT_ROOT / "data" / "grid_predictions_comparison.csv"
DEFAULT_GRID_GPKG = PROJECT_ROOT / "data" / "grid_1km_all.gpkg"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "visualizations" / "three_model_prediction_comparison.png"


def _normalize_cell_id(value: str) -> str:
    parts = str(value).split("_")
    if len(parts) == 3 and parts[0] == "cell":
        return f"cell_{int(parts[1]):04d}_{int(parts[2]):04d}"
    return str(value)


def load_prediction_map(predictions_csv: Path, grid_gpkg: Path) -> gpd.GeoDataFrame:
    predictions = pd.read_csv(predictions_csv)
    grid = gpd.read_file(grid_gpkg)

    if "cell_id" not in grid.columns:
        raise KeyError(f"`cell_id` not found in {grid_gpkg}")

    grid = grid.copy()
    grid["cell_id"] = grid["cell_id"].map(_normalize_cell_id)

    if "cell_id" not in predictions.columns:
        raise KeyError(f"`cell_id` not found in {predictions_csv}")

    predictions = predictions.copy()
    predictions["cell_id"] = predictions["cell_id"].map(_normalize_cell_id)

    merged = grid.merge(predictions, on="cell_id", how="left")
    if merged.empty:
        raise ValueError("Merged grid/prediction dataset is empty")

    return merged


def create_figure(gdf: gpd.GeoDataFrame, output_path: Path) -> None:
    model_specs = [
        ("pred_scaled_catboost", "CatBoost"),
        ("pred_scaled_rf", "Random Forest"),
        ("cnn_pred", "CNN"),
    ]

    missing_cols = [col for col, _ in model_specs if col not in gdf.columns]
    if missing_cols:
        raise KeyError(f"Missing prediction columns: {', '.join(missing_cols)}")

    all_values = pd.concat([gdf[col] for col, _ in model_specs], ignore_index=True).dropna()
    if all_values.empty:
        raise ValueError("No prediction values found to plot")

    vmin = float(all_values.min())
    vmax = float(all_values.max())

    fig, axes = plt.subplots(1, 3, figsize=(20, 7.5))
    fig.subplots_adjust(left=0.02, right=0.985, top=0.84, bottom=0.16, wspace=0.1)
    cmap = "YlOrRd"
    plot_collection = None

    for ax, (column, title) in zip(axes, model_specs):
        subset = gdf.dropna(subset=[column])
        plot_collection = subset.plot(
            column=column,
            ax=ax,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            linewidth=0,
            legend=False,
        )
        ax.set_title(
            f"{title}\n"
            f"n={subset[column].notna().sum()} | "
            f"mean={subset[column].mean():.3f}",
            fontsize=13,
            pad=12,
        )
        ax.set_axis_off()

    fig.suptitle(
        "Grid-Level Poverty Prediction Comparison",
        fontsize=18,
        fontweight="bold",
    )
    if plot_collection is None:
        raise RuntimeError("Plot collection was not created")

    colorbar = fig.colorbar(
        plot_collection.collections[0],
        ax=axes,
        orientation="horizontal",
        fraction=0.035,
        pad=0.08,
    )
    colorbar.set_label(
        f"Predicted poverty value | shared scale {vmin:.3f} to {vmax:.3f}",
        fontsize=11,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predictions-csv",
        type=Path,
        default=DEFAULT_PREDICTIONS_CSV,
        help="Merged prediction CSV containing CatBoost, RF, and CNN columns.",
    )
    parser.add_argument(
        "--grid-gpkg",
        type=Path,
        default=DEFAULT_GRID_GPKG,
        help="Grid GeoPackage used to draw the polygons.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination PNG path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gdf = load_prediction_map(args.predictions_csv, args.grid_gpkg)
    create_figure(gdf, args.output)
    print(f"Saved comparison figure to: {args.output}")


if __name__ == "__main__":
    main()
