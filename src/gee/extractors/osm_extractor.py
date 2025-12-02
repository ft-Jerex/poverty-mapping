"""Placeholder OSM accessibility extractor.

For now this serves as a stub where you can later plug in preprocessed
OSM rasters (e.g. road density, distance-to-road) that you host as
assets in GEE or on Drive. The interface matches the other extractors
so it can be used in the pipeline.
"""

from typing import Dict, Any, Optional
import ee
import logging
from .base_extractor import BaseExtractor

logger = logging.getLogger(__name__)


class OSMExtractor(BaseExtractor):
    """Stub extractor for OSM-based accessibility surfaces."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "collection": None,  # to be set when you have a hosted OSM raster
            "export_params": {
                "scale": 100,
                "crs": "EPSG:4326",
                "maxPixels": 1e13,
            },
            "band_names": [],
        }
        if config:
            default_config.update(config)
        super().__init__(default_config)

    def get_image_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        # Placeholder: if you provide a static OSM raster asset id in config["collection"],
        # this will wrap it as a single-image collection, similar to DEMExtractor.
        if not self.config.get("collection"):
            raise ValueError("OSMExtractor.config['collection'] must be set to a valid asset ID.")
        image = ee.Image(self.config["collection"])
        if "geometry" in kwargs:
            image = image.clip(kwargs["geometry"])
        return ee.ImageCollection([image])

    def process_image(self, image: ee.Image) -> ee.Image:
        # For now just cast all bands to float; real logic depends on your OSM raster schema.
        return image.toFloat()
