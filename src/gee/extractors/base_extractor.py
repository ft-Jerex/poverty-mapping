"""Base class for GEE data extractors."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union, List
import ee
import time
import logging
from pathlib import Path
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BaseExtractor(ABC):
    """Base class for extracting data from Google Earth Engine."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the extractor with configuration.
        
        Args:
            config: Configuration dictionary with parameters for data extraction
        """
        self.config = config
        self.dataset_name = self.__class__.__name__
        self.export_params = {
            'driveFolder': 'povMap_exports',
            'fileFormat': 'GeoTIFF',
            'skipEmptyTiles': True,
            'maxPixels': 1e13,
        }
        self.export_params.update(config.get('export_params', {}))
    
    @abstractmethod
    def get_image_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        """Get the image collection for the specified date range.
        
        Args:
            start_date: Start date in 'YYYY-MM-DD' format
            end_date: End date in 'YYYY-MM-DD' format
            **kwargs: Additional parameters for filtering the collection
            
        Returns:
            ee.ImageCollection: Filtered image collection
        """
        pass
    
    @abstractmethod
    def process_image(self, image: ee.Image) -> ee.Image:
        """Process a single image from the collection.
        
        Args:
            image: Input image to process
            
        Returns:
            ee.Image: Processed image
        """
        pass
    
    def get_processed_collection(self, start_date: str, end_date: str, **kwargs) -> ee.ImageCollection:
        """Get and process the image collection.
        
        Args:
            start_date: Start date in 'YYYY-MM-DD' format
            end_date: End date in 'YYYY-MM-DD' format
            **kwargs: Additional parameters for filtering the collection
            
        Returns:
            ee.ImageCollection: Processed image collection
        """
        collection = self.get_image_collection(start_date, end_date, **kwargs)
        return collection.map(self.process_image)
    
    def export_image(
        self, 
        image: ee.Image, 
        description: str,
        folder: Optional[str] = None,
        file_name: Optional[str] = None,
        **export_params
    ) -> ee.batch.Task:
        """Export an image to Google Drive.
        
        Args:
            image: Image to export
            description: Description for the export task
            folder: Google Drive folder to export to (default: self.export_params['driveFolder'])
            file_name: Output file name (without extension)
            **export_params: Additional export parameters
            
        Returns:
            ee.batch.Task: The export task
        """
        # Merge default export params with any overrides
        params = {**self.export_params, **export_params}

        # Use folder override if provided
        if folder:
            params['driveFolder'] = folder

        # GEE can error if dimensions / fileDimensions are not clean multiples
        # of its internal shard size (256). For robustness, we avoid specifying
        # explicit dimensions here and rely on scale + region instead.
        if 'dimensions' in params:
            logger.warning("Removing 'dimensions' from export params to avoid shard size errors.")
            params.pop('dimensions', None)

        if 'fileDimensions' in params:
            logger.warning("Removing 'fileDimensions' from export params to avoid shard size errors.")
            params.pop('fileDimensions', None)

        # Ensure we always have a sane scale and maxPixels
        if 'scale' not in params:
            params['scale'] = 10
        if 'maxPixels' not in params:
            params['maxPixels'] = 1e13

        if not file_name:
            file_name = f"{self.dataset_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        task = ee.batch.Export.image.toDrive(
            image=image,
            description=description,
            fileNamePrefix=file_name,
            **params
        )

        task.start()
        logger.info(f"Started export task: {description} (ID: {task.id})")
        return task
    
    def monitor_tasks(self, tasks: List[ee.batch.Task], poll_interval: int = 60) -> None:
        """Monitor the status of export tasks.
        
        Args:
            tasks: List of tasks to monitor
            poll_interval: Time in seconds between status checks
        """
        import time
        from tqdm import tqdm
        
        task_ids = [task.id for task in tasks]
        statuses = [task.status() for task in tasks]
        
        with tqdm(total=len(tasks), desc="Processing exports") as pbar:
            while any(status['state'] in ['READY', 'RUNNING'] for status in statuses):
                time.sleep(poll_interval)
                
                for i, (task, status) in enumerate(zip(tasks, statuses)):
                    if status['state'] in ['COMPLETED', 'FAILED', 'CANCELLED']:
                        continue
                        
                    new_status = task.status()
                    if new_status['state'] != status['state']:
                        logger.info(f"Task {task.id} status: {status['state']} -> {new_status['state']}")
                        
                        if new_status['state'] in ['COMPLETED', 'FAILED', 'CANCELLED']:
                            pbar.update(1)
                            
                            if new_status['state'] == 'FAILED':
                                logger.error(f"Task {task.id} failed: {new_status.get('error_message', 'Unknown error')}")
                    
                    statuses[i] = new_status
        
        logger.info("All tasks completed")
