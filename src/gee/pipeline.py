"""Quarterly GEE extraction pipeline for multiple datasets."""
from typing import Dict, Any, Optional, List
import logging
import ee

from .config import initialize_gee, get_study_area
from .extractors.sentinel2_extractor import Sentinel2Extractor
from .extractors.landsat_extractor import LandsatExtractor
from .extractors.modis_extractor import MODISExtractor
from .extractors.chirps_extractor import CHIRPSExtractor
from .extractors.dem_extractor import DEMExtractor

logger = logging.getLogger(__name__)


class QuarterlyPipeline:
    """Run quarterly composites and exports for multiple datasets."""

    def __init__(self, geometry: Optional[ee.Geometry] = None):
        initialize_gee()
        self.geometry = geometry or get_study_area()

        self.extractors = {
            "sentinel2": Sentinel2Extractor(),
            "landsat": LandsatExtractor(),
            "modis": MODISExtractor(),
            "chirps": CHIRPSExtractor(),
            "dem": DEMExtractor(),
        }

    def run_quarter(self, year: int, quarter: int, datasets: Optional[List[str]] = None,
                    export_folder: str = "povMap_exports") -> Dict[str, ee.batch.Task]:
        datasets = datasets or list(self.extractors.keys())
        tasks: Dict[str, ee.batch.Task] = {}

        for name in datasets:
            extractor = self.extractors.get(name)
            if not extractor:
                logger.warning(f"Unknown dataset '{name}', skipping.")
                continue

            logger.info(f"Running quarterly composite for {name}: year={year}, quarter={quarter}")
            composite = extractor.get_quarterly_composite(year, quarter, geometry=self.geometry)

            desc = f"{name.upper()}_Q{quarter}_{year}"
            fname = f"{name.lower()}_q{quarter}_{year}"

            task = extractor.export_image(
                image=composite,
                description=desc,
                folder=export_folder,
                file_name=fname,
                scale=extractor.config.get("export_params", {}).get("scale", 100),
                region=self.geometry,
                maxPixels=extractor.config.get("export_params", {}).get("maxPixels", 1e13),
            )
            tasks[name] = task

        return tasks


__all__ = ["QuarterlyPipeline"]
