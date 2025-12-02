"""CHIRPS rainfall data extractor for Google Earth Engine."""
from typing import Dict, Any, Optional
import ee
import logging
from .base_extractor import BaseExtractor

logger = logging.getLogger(__name__)


class CHIRPSExtractor(BaseExtractor):
    """Extractor for CHIRPS daily rainfall data."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "collection": "UCSB-CHG/CHIRPS/DAILY",
            "export_params": {
                "scale": 5000,
                "crs": "EPSG:4326",
                "maxPixels": 1e13,
            },
            "band_names": ["precipitation"],
            "composite_method": "sum",  # sum rainfall over quarter
        }
        if config:
            default_config.update(config)
        super().__init__(default_config)

    def get_image_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        collection = ee.ImageCollection(self.config["collection"]).filterDate(start_date, end_date)
        for key, value in kwargs.items():
            if key in ["geometry", "filterBounds"]:
                collection = collection.filterBounds(value)
            else:
                collection = collection.filter(ee.Filter.eq(key, value))
        return collection

    def process_image(self, image: ee.Image) -> ee.Image:
        try:
            img = image.select(self.config["band_names"]).toFloat()
            return img
        except Exception as e:
            logger.error(f"Error processing CHIRPS image: {e}")
            raise

    def get_quarterly_composite(self, year: int, quarter: int, geometry: Optional[ee.Geometry] = None) -> ee.Image:
        start_month = (quarter - 1) * 3 + 1
        start_date = f"{year}-{start_month:02d}-01"
        if quarter < 4:
            end_month = start_month + 3
            end_date = f"{year}-{end_month:02d}-01"
        else:
            end_date = f"{year+1}-01-01"

        collection = self.get_image_collection(start_date, end_date, geometry=geometry)
        processed = collection.map(self.process_image)
        if self.config["composite_method"] == "sum":
            composite = processed.sum()
        elif self.config["composite_method"] == "median":
            composite = processed.median()
        else:
            composite = processed.mean()

        composite = composite.set({
            "system:time_start": ee.Date(start_date).millis(),
            "period_start": start_date,
            "period_end": end_date,
            "composite_method": self.config["composite_method"],
            "quarter": quarter,
            "year": year,
        })
        return composite
