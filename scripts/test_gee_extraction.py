import sys
import os
import logging
import ee
from pathlib import Path

# Add the project root to the Python path
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from src.gee import Sentinel2Extractor
from src.gee.config import initialize_gee, get_study_area

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def test_sentinel2_extraction():
    """Test Sentinel-2 data extraction with a small area and short time period."""
    try:
        logger.info("=== Starting Sentinel-2 extraction test...")
        
        # Initialize GEE
        logger.info("=== Initializing GEE...")
        initialize_gee()
        
        # Create a small test area (smaller than the full study area for testing)
        test_area = get_study_area().buffer(-0.05).bounds()  # Smaller area for testing
        
        # Create extractor with test configuration
        # Note: we do NOT pass any dimension-related settings here to avoid
        # shard-size errors in GEE. Export will be controlled via scale/region
        # arguments in export_image.
        config = {
            'cloud_cover_max': 30  # Be more permissive with cloud cover for testing
        }
        extractor = Sentinel2Extractor(config)
        
        # Test date range (recent 1 month)
        test_end = "2023-10-01"
        test_start = "2023-09-01"
        
        logger.info(f"=== Getting image collection from {test_start} to {test_end}")
        collection = extractor.get_image_collection(
            start_date=test_start,
            end_date=test_end,
            geometry=test_area
        )
        
        # Count images in collection
        count = collection.size().getInfo()
        logger.info(f"=== Found {count} images in the collection")
        
        if count == 0:
            logger.warning("⚠️ No images found in the collection. Check date range and area of interest.")
            return False
            
        # Process the first image
        logger.info("=== Processing first image...")
        first_image = ee.Image(collection.first())
        processed = extractor.process_image(first_image)
        
        # Check if processing was successful
        if processed is None:
            logger.error("Image processing failed")
            return False
            
        logger.info("Image processing successful")
        
        # Test export (small area, low resolution)
        logger.info("=== Testing export (small area, low resolution)...")
        task = extractor.export_image(
            image=processed,
            description="TEST_Sentinel2_Export",
            file_name="test_export",
            folder="povMap_test_exports",
            scale=100,  # Low resolution for testing
            region=test_area,
            maxPixels=1e6  # Small export for testing
        )
        
        logger.info(f"=== Export task started with ID: {task.id}")
        logger.info("=== Check your Google Earth Engine Tasks console to monitor progress")
        logger.info("[SUCCESS] Test completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"[ERROR] Test failed: {str(e)}", exc_info=True)
        return False

if __name__ == "__main__":
    test_sentinel2_extraction()
