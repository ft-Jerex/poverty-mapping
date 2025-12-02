"""Landsat 8/9 data extractor for Google Earth Engine."""
from typing import Dict, Any, Optional
import ee
import logging
from .base_extractor import BaseExtractor

logger = logging.getLogger(__name__)


class LandsatExtractor(BaseExtractor):
    """Extractor for Landsat surface reflectance data (Collection 2 Tier 1)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "collection": "LANDSAT/LC08/C02/T1_L2",  # You can swap to LC09 if needed
            "cloud_cover_max": 30,
            "export_params": {
                "scale": 30,
                "crs": "EPSG:4326",
                "maxPixels": 1e13,
            },
            "band_names": [
                "SR_B2", "SR_B3", "SR_B4", "SR_B5",  # Blue, Green, Red, NIR
                "SR_B6", "SR_B7",  # SWIR1, SWIR2
                "QA_PIXEL",
            ],
            "indices": ["NDVI", "NDWI", "NDBI"],
            "composite_method": "median",
        }
        if config:
            default_config.update(config)
        super().__init__(default_config)

    def get_image_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        collection = ee.ImageCollection(self.config["collection"]).filterDate(start_date, end_date)

        if "cloud_cover_max" in self.config:
            collection = collection.filter(
                ee.Filter.lte("CLOUD_COVER", self.config["cloud_cover_max"])
            )

        for key, value in kwargs.items():
            if key in ["geometry", "filterBounds"]:
                collection = collection.filterBounds(value)
            else:
                collection = collection.filter(ee.Filter.eq(key, value))

        return collection

    def _apply_scale_factors(self, image: ee.Image) -> ee.Image:
        # Landsat Surface Reflectance scale factor
        optical = image.select(["SR_B.*"]).multiply(0.0000275).add(-0.2)
        optical = optical.copyProperties(image, image.propertyNames())
        return optical

    def calculate_indices(self, image: ee.Image) -> ee.Image:
        result = image
        for index in self.config.get("indices", []):
            if index == "NDVI":
                ndvi = image.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")
                result = result.addBands(ndvi)
            elif index == "NDWI":
                ndwi = image.normalizedDifference(["SR_B3", "SR_B5"]).rename("NDWI")
                result = result.addBands(ndwi)
            elif index == "NDBI":
                ndbi = image.normalizedDifference(["SR_B6", "SR_B5"]).rename("NDBI")
                result = result.addBands(ndbi)
        return result.toFloat()

    def process_image(self, image: ee.Image) -> ee.Image:
        try:
            image = image.select(self.config["band_names"])
            image = self._apply_scale_factors(image)
            image = self.calculate_indices(image)
            return image.toFloat()
        except Exception as e:
            logger.error(f"Error processing Landsat image: {e}")
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
