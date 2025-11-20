"""MODIS land surface data extractor for Google Earth Engine."""
from typing import Dict, Any, Optional
import ee
import logging
from .base_extractor import BaseExtractor

logger = logging.getLogger(__name__)


class MODISExtractor(BaseExtractor):
    """Extractor for MODIS land surface products (e.g., NDVI)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "collection": "MODIS/006/MOD13Q1",  # 16-day NDVI/EVI, 250m
            "export_params": {
                "scale": 250,
                "crs": "EPSG:4326",
                "maxPixels": 1e13,
            },
            "band_names": ["NDVI"],
            "composite_method": "mean",
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
            # MODIS NDVI is scaled by 0.0001
            ndvi = image.select("NDVI").multiply(0.0001).rename("NDVI")
            ndvi = ndvi.toFloat()
            return ndvi
        except Exception as e:
            logger.error(f"Error processing MODIS image: {e}")
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
        if self.config["composite_method"] == "median":
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
