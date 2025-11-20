"""Google Earth Engine data extraction and processing module.

This module provides functionality to extract and process satellite imagery and other
global datasets from Google Earth Engine for poverty mapping applications.
"""

__version__ = "0.2.0"

# Import extractors and pipeline for easier access
from .extractors.sentinel2_extractor import Sentinel2Extractor
from .extractors.landsat_extractor import LandsatExtractor
from .extractors.modis_extractor import MODISExtractor
from .extractors.chirps_extractor import CHIRPSExtractor
from .extractors.dem_extractor import DEMExtractor
from .extractors.osm_extractor import OSMExtractor
from .pipeline import QuarterlyPipeline

__all__ = [
    'Sentinel2Extractor',
    'LandsatExtractor',
    'MODISExtractor',
    'CHIRPSExtractor',
    'DEMExtractor',
    'OSMExtractor',
    'QuarterlyPipeline',
]
