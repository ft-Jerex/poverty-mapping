"""DEM data extractor for Google Earth Engine."""
from typing import Dict, Any, Optional
import ee
import logging
from .base_extractor import BaseExtractor

logger = logging.getLogger(__name__)


class DEMExtractor(BaseExtractor):
    """Extractor for SRTM DEM data."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "collection": "USGS/SRTMGL1_003",
            "export_params": {
                "scale": 30,
                "crs": "EPSG:4326",
                "maxPixels": 1e13,
            },
            "band_names": ["elevation"],
        }
        if config:
            default_config.update(config)
        super().__init__(default_config)

    def get_image_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        # SRTM is static; ignore dates and just return a single image as collection
        image = ee.Image(self.config["collection"])
        if "geometry" in kwargs:
            image = image.clip(kwargs["geometry"])
        collection = ee.ImageCollection([image])
        return collection

    def process_image(self, image: ee.Image) -> ee.Image:
        try:
            img = image.select(self.config["band_names"]).toFloat()
            return img
        except Exception as e:
            logger.error(f"Error processing DEM image: {e}")
            raise

    def get_quarterly_composite(self, year: int, quarter: int, geometry: Optional[ee.Geometry] = None) -> ee.Image:
        # DEM is static; just return the processed image once
        collection = self.get_image_collection("2000-01-01", "2001-01-01", geometry=geometry)
        processed = collection.map(self.process_image)
        composite = processed.first()
        composite = ee.Image(composite).set({
            "system:time_start": ee.Date.fromYMD(year, 1, 1).millis(),
            "period_start": f"{year}-01-01",
            "period_end": f"{year}-12-31",
            "quarter": quarter,
            "year": year,
        })
        return composite
