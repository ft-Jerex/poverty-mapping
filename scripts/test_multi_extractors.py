"""Smoke test for multiple GEE extractors and quarterly pipeline."""
import sys
import logging
from pathlib import Path
import ee

# Add project root
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.gee.config import initialize_gee, get_study_area
from src.gee.pipeline import QuarterlyPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    initialize_gee()
    geom = get_study_area()

    pipeline = QuarterlyPipeline(geometry=geom)
    year = 2023
    quarter = 3

    logger.info("Running multi-dataset quarterly pipeline smoke test...")
    tasks = pipeline.run_quarter(year, quarter, datasets=["sentinel2", "dem"])

    for name, task in tasks.items():
        logger.info(f"Started task for {name}: ID={task.id}")

    logger.info("Smoke test complete. Monitor tasks in the GEE Tasks console.")


if __name__ == "__main__":
    main()
