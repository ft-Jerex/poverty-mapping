"""CLI wrapper to run the quarterly GEE extraction pipeline."""
from __future__ import annotations

import argparse
from typing import List

from src.gee.pipeline import QuarterlyPipeline
from src.gee.config import get_study_area


def run_quarter(year: int, quarter: int, datasets: List[str]) -> None:
    geom = get_study_area()
    pipeline = QuarterlyPipeline(geometry=geom)
    tasks = pipeline.run_quarter(year, quarter, datasets=datasets)

    for name, task in tasks.items():
        print(f"Started task for {name}: ID={task.id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run quarterly GEE extraction pipeline.")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], required=True)
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=["sentinel2", "landsat", "modis", "chirps", "dem"],
        help="Datasets to run (default: all core datasets)",
    )
    args = parser.parse_args()

    run_quarter(args.year, args.quarter, args.datasets)


if __name__ == "__main__":  # pragma: no cover
    main()
