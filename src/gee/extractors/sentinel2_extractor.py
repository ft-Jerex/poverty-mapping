"""Sentinel-2 data extractor for Google Earth Engine."""
from typing import Dict, Any, Optional, List
import ee
import logging
from pathlib import Path
from datetime import datetime, timedelta
from .base_extractor import BaseExtractor

logger = logging.getLogger(__name__)

class Sentinel2Extractor(BaseExtractor):
    """Extractor for Sentinel-2 surface reflectance data."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the Sentinel-2 extractor.
        
        Args:
            config: Configuration dictionary with parameters for data extraction
        """
        default_config = {
            'collection': 'COPERNICUS/S2_SR',  # Surface Reflectance
            'cloud_cover_max': 20,  # Maximum cloud cover percentage
            'export_params': {
                'scale': 10,  # Native resolution for most bands
                'crs': 'EPSG:4326',
                'fileDimensions': [256, 256],  # Tile size
                'maxPixels': 1e13,
            },
            'band_names': [
                'B2', 'B3', 'B4', 'B8',  # RGB + NIR (10m)
                'B5', 'B6', 'B7', 'B8A', 'B11', 'B12',  # Red edge + SWIR (20m)
                'SCL'  # Scene Classification Layer
            ],
            'output_bands': {
                'B2': 'blue',
                'B3': 'green',
                'B4': 'red',
                'B8': 'nir',
                'B5': 'red_edge1',
                'B6': 'red_edge2',
                'B7': 'red_edge3',
                'B8A': 'red_edge4',
                'B11': 'swir1',
                'B12': 'swir2',
                'SCL': 'scl'
            },
            'indices': ['NDVI', 'NDWI', 'NDBI'],
            'composite_method': 'median',  # 'median' or 'mean'
        }
        
        if config:
            default_config.update(config)
            
        super().__init__(default_config)
    
    def get_image_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        """Get the Sentinel-2 image collection for the specified date range.
        
        Args:
            start_date: Start date in 'YYYY-MM-DD' format
            end_date: End date in 'YYYY-MM-DD' format
            **kwargs: Additional parameters for filtering the collection
            
        Returns:
            ee.ImageCollection: Filtered Sentinel-2 image collection
        """
        collection = ee.ImageCollection(self.config['collection'])
        
        # Filter by date and region
        collection = collection.filterDate(start_date, end_date)
        
        # Filter by cloud cover
        if 'cloud_cover_max' in self.config:
            collection = collection.filter(
                ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', self.config['cloud_cover_max'])
            )
        
        # Apply additional filters if provided
        for key, value in kwargs.items():
            if key in ['geometry', 'filterBounds']:
                collection = collection.filterBounds(value)
            else:
                collection = collection.filter(ee.Filter.eq(key, value))
        
        return collection
    
    def calculate_indices(self, image: ee.Image) -> ee.Image:
        """Calculate spectral indices for the image with consistent Float32 output."""
        try:
            # Initialize with the original image
            result = image
            
            # Calculate each requested index
            for index in self.config.get('indices', []):
                if index == 'NDVI':
                    ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI').toFloat()
                    result = result.addBands(ndvi)
                elif index == 'NDWI':
                    ndwi = image.normalizedDifference(['B3', 'B8']).rename('NDWI').toFloat()
                    result = result.addBands(ndwi)
                elif index == 'NDBI':
                    ndbi = image.normalizedDifference(['B11', 'B8']).rename('NDBI').toFloat()
                    result = result.addBands(ndbi)
            
            # Ensure all bands are Float32
            return result.toFloat()
            
        except Exception as e:
            logger.error(f"Error calculating indices: {str(e)}")
            raise
    
    def mask_clouds(self, image: ee.Image) -> ee.Image:
        """Mask clouds and cloud shadows using the SCL band.

        All bands, including SCL, are kept as Float32 so that exports have
        consistent dtypes.
        """
        try:
            scl = image.select('SCL')
            cloud_mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))

            # Mask all bands using the cloud mask
            masked = image.updateMask(cloud_mask)

            # Cast everything to Float32, including SCL
            return masked.toFloat()

        except Exception as e:
            logger.error(f"Error applying cloud mask: {str(e)}")
            raise
    
    def process_image(self, image: ee.Image) -> ee.Image:
        """Process a single Sentinel-2 image with server-safe operations.
        
        All output bands are Float32 to ensure export compatibility.
        """
        try:
            # 1. Select base bands and cast to float
            img = image.select(self.config['band_names']).toFloat()

            # 2. Add spectral indices (Float32)
            img = self.calculate_indices(img)

            # 3. Apply cloud masking if SCL is available
            if 'SCL' in self.config['band_names']:
                img = self.mask_clouds(img)

            # 4. Ensure final dtype is Float32
            img = img.toFloat()

            # 5. Add metadata
            date = ee.Date(image.get('system:time_start'))
            img = img.set({
                'system:time_start': date.millis(),
                'date_acquired': date.format('YYYY-MM-dd'),
                'year': date.get('year'),
                'month': date.get('month'),
                'day': date.get('day')
            })

            return img

        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            raise
    
    def get_quarterly_composite(
        self, 
        year: int, 
        quarter: int, 
        geometry: Optional[ee.Geometry] = None
    ) -> ee.Image:
        """Get a quarterly composite image.
        
        Args:
            year: Year (e.g., 2023)
            quarter: Quarter (1-4)
            geometry: Optional geometry to filter the collection
            
        Returns:
            ee.Image: Quarterly composite image
        """
        # Calculate date range for the quarter
        start_month = (quarter - 1) * 3 + 1
        start_date = f"{year}-{start_month:02d}-01"
        
        if quarter < 4:
            end_month = start_month + 3
            end_date = f"{year}-{end_month:02d}-01"
        else:
            end_date = f"{year+1}-01-01"
        
        # Get and process the collection
        collection = self.get_image_collection(start_date, end_date, geometry=geometry)
        processed_collection = collection.map(self.process_image)
        
        # Create composite and cast to Float32
        if self.config['composite_method'] == 'median':
            composite = processed_collection.median()
        else:  # default to mean
            composite = processed_collection.mean()

        composite = composite.toFloat()
        
        # Add metadata
        composite = composite.set({
            'system:time_start': ee.Date(start_date).millis(),
            'period_start': start_date,
            'period_end': end_date,
            'composite_method': self.config['composite_method'],
            'quarter': quarter,
            'year': year
        })
        
        return composite
