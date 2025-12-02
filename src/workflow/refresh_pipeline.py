"""
Refresh Pipeline Orchestrator

Coordinates the full data refresh workflow:
1. Extract data from Google Earth Engine (geospatial_prep)
2. Preprocess grid data (attach barangay info)
3. Run model inference (CatBoost, RF)
4. Merge predictions into format expected by web app
5. Update status for frontend polling

This module can be run as a background process triggered by the /api/refresh endpoint.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, Dict, Any, Callable
import time
import tempfile

# Status file for frontend polling
STATUS_FILE = Path(__file__).parent.parent.parent / "data" / "refresh_status.json"

# Global lock to prevent concurrent refreshes
_refresh_lock = threading.Lock()
_current_refresh_thread: Optional[threading.Thread] = None


def update_status(
    phase: str,
    message: str = "",
    progress: int = 0,
    total_steps: int = 100,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Update refresh status for frontend polling."""
    data = {
        "phase": phase,
        "message": message,
        "progress": progress,
        "total_steps": total_steps,
        "updated_at": datetime.now().isoformat(),
    }
    if error:
        data["error"] = error
    if extra:
        data.update(extra)
    
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def get_status() -> Dict[str, Any]:
    """Get current refresh status."""
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text())
    except Exception:
        pass
    return {"phase": "IDLE", "message": "No refresh in progress", "progress": 0}


def is_refresh_running() -> bool:
    """Check if a refresh is currently in progress."""
    global _current_refresh_thread
    if _current_refresh_thread is not None and _current_refresh_thread.is_alive():
        return True
    status = get_status()
    return status.get("phase") not in ("IDLE", "COMPLETED", "ERROR", None)


class RefreshPipeline:
    """
    Orchestrates the complete data refresh workflow.
    """
    
    def __init__(
        self,
        povmap_backend_dir: Path,
        webapp_data_dir: Path,
        models_dir: Path,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        callback: Optional[Callable[[str, int], None]] = None,
    ):
        """
        Initialize the refresh pipeline.
        
        Args:
            povmap_backend_dir: Path to povmapbackend directory (contains scripts)
            webapp_data_dir: Path to poverty-mapping-withbackend/data (output location)
            models_dir: Path to trained models
            start_date: Start date for GEE data extraction (YYYY-MM-DD), default 1 year ago
            end_date: End date for GEE data extraction (YYYY-MM-DD), default today
            callback: Optional callback function(message, progress) for status updates
        """
        self.povmap_backend = Path(povmap_backend_dir)
        self.webapp_data = Path(webapp_data_dir)
        self.models_dir = Path(models_dir)
        self.callback = callback
        
        # Set date range (default: 1 year leading up to today)
        if end_date is None:
            self.end_date = datetime.now().strftime("%Y-%m-%d")
        else:
            self.end_date = end_date
            
        if start_date is None:
            end_dt = datetime.strptime(self.end_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=365)
            self.start_date = start_dt.strftime("%Y-%m-%d")
        else:
            self.start_date = start_date
        
        # Verify paths exist
        if not self.povmap_backend.exists():
            raise FileNotFoundError(f"povmapbackend not found at {self.povmap_backend}")
        if not self.models_dir.exists():
            raise FileNotFoundError(f"Models directory not found at {self.models_dir}")
    
    def _update(self, phase: str, message: str, progress: int, **kwargs):
        """Update status and call callback if provided."""
        update_status(phase, message, progress, **kwargs)
        if self.callback:
            self.callback(message, progress)
    
    def run_geospatial_extraction(self) -> bool:
        """
        Run GEE data extraction with the configured date range.
        
        Creates a temporary modified version of geospatial_prep.py with dynamic dates.
        """
        self._update("GEE_EXTRACTION", f"Extracting GEE data for {self.start_date} to {self.end_date}", 10)
        
        script_path = self.povmap_backend / "geospatial_prep.py"
        
        if not script_path.exists():
            self._update("ERROR", f"geospatial_prep.py not found at {script_path}", 10, error="Script not found")
            return False
        
        try:
            # Read the original script
            original_content = script_path.read_text(encoding='utf-8')
            
            # Replace hardcoded dates with our dynamic dates
            modified_content = original_content.replace(
                'start_date = "2024-01-01"',
                f'start_date = "{self.start_date}"'
            ).replace(
                'end_date = "2024-12-31"',
                f'end_date = "{self.end_date}"'
            )
            
            # Write to a temporary file
            temp_script = self.povmap_backend / "_geospatial_prep_temp.py"
            temp_script.write_text(modified_content, encoding='utf-8')
            
            try:
                # Run the modified script with UTF-8 encoding to handle emoji characters
                result = subprocess.run(
                    [sys.executable, "-X", "utf8", str(temp_script)],
                    cwd=str(self.povmap_backend),
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=1800,  # 30 minute timeout
                    env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'}
                )
                
                if result.returncode != 0:
                    self._update(
                        "ERROR",
                        f"GEE extraction failed: {result.stderr[:500]}",
                        10,
                        error=result.stderr[:1000]
                    )
                    return False
                
                self._update("GEE_EXTRACTION_DONE", "GEE data extraction complete", 30)
                return True
                
            finally:
                # Clean up temp file
                if temp_script.exists():
                    temp_script.unlink()
            
        except subprocess.TimeoutExpired:
            self._update("ERROR", "GEE extraction timed out after 30 minutes", 10, error="Timeout")
            return False
        except Exception as e:
            self._update("ERROR", f"GEE extraction error: {str(e)}", 10, error=str(e))
            return False
            return False
    
    def run_preprocessing(self) -> bool:
        """
        Run grid data preprocessing.
        
        This calls preprocess_grid_data.py to attach barangay info.
        """
        self._update("PREPROCESSING", "Preprocessing grid data...", 35)
        
        script_path = self.povmap_backend / "preprocess_grid_data.py"
        
        if not script_path.exists():
            self._update("ERROR", f"preprocess_grid_data.py not found", 35, error="Script not found")
            return False
        
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(self.povmap_backend),
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
            )
            
            if result.returncode != 0:
                self._update(
                    "ERROR",
                    f"Preprocessing failed: {result.stderr[:500]}",
                    35,
                    error=result.stderr[:1000]
                )
                return False
            
            self._update("PREPROCESSING_DONE", "Grid preprocessing complete", 50)
            return True
            
        except subprocess.TimeoutExpired:
            self._update("ERROR", "Preprocessing timed out", 35, error="Timeout")
            return False
        except Exception as e:
            self._update("ERROR", f"Preprocessing error: {str(e)}", 35, error=str(e))
            return False
    
    def run_model_inference(self) -> tuple[bool, bool]:
        """
        Run CatBoost, RF, and CNN model inference.
        
        Returns:
            (success, used_inference_module): success status and whether inference.py was used
        """
        self._update("INFERENCE", "Running model inference...", 55)
        
        try:
            from src.model.inference import run_all_models
            
            preprocessed_csv = self.povmap_backend / "assets" / "grid_with_comprehensive_data.csv"
            
            if not preprocessed_csv.exists():
                self._update("ERROR", "Preprocessed data not found", 55, error="Missing input file")
                return False, False
            
            output_dir = self.webapp_data
            
            outputs = run_all_models(
                preprocessed_csv=preprocessed_csv,
                models_dir=self.models_dir,
                output_dir=output_dir,
                povmap_backend_dir=self.povmap_backend,
            )
            
            self._update(
                "INFERENCE_DONE",
                f"Model inference complete. Generated {len(outputs)} outputs.",
                70,
                extra={"outputs": [str(p) for p in outputs.values()]}
            )
            
            # Run CNN inference if models exist
            self.run_cnn_inference()
            
            # Inference module creates grid_predictions_comparison.csv directly
            # Skip merge step and mark as complete
            self._update("MERGING_DONE", "Predictions already merged by inference module", 90)
            return True, True  # Success + used inference module
            
        except ImportError:
            # Fallback: run training scripts which also save predictions
            self._update("INFERENCE", "Running CatBoost training/inference...", 55)
            
            try:
                # Run CatBoost
                catboost_script = self.povmap_backend / "train_catboost_model.py"
                if catboost_script.exists():
                    result = subprocess.run(
                        [sys.executable, str(catboost_script)],
                        cwd=str(self.povmap_backend),
                        capture_output=True,
                        text=True,
                        timeout=1200,
                    )
                    if result.returncode != 0:
                        print(f"CatBoost warning: {result.stderr[:500]}")
                
                self._update("INFERENCE", "Running RF training/inference...", 65)
                
                # Run RF
                rf_script = self.povmap_backend / "train_rf_model.py"
                if rf_script.exists():
                    result = subprocess.run(
                        [sys.executable, str(rf_script)],
                        cwd=str(self.povmap_backend),
                        capture_output=True,
                        text=True,
                        timeout=1200,
                    )
                    if result.returncode != 0:
                        print(f"RF warning: {result.stderr[:500]}")
                
                # Run CNN inference
                self._update("INFERENCE", "Running CNN inference...", 70)
                self.run_cnn_inference()
                
                self._update("INFERENCE_DONE", "Model inference complete", 75)
                return True, False  # Success + used training scripts (not inference module)
                
            except Exception as e:
                self._update("ERROR", f"Inference error: {str(e)}", 60, error=str(e))
                return False, False
        except Exception as e:
            self._update("ERROR", f"Inference error: {str(e)}", 55, error=str(e))
            return False, False
    
    def run_cnn_inference(self) -> bool:
        """
        Run CNN model inference using cnn_data_preprocessing.py.
        Creates all_cells_predictions_1km.csv for webapp.
        """
        try:
            cnn_script = self.povmap_backend / "cnn_data_preprocessing.py"
            if not cnn_script.exists():
                print("CNN script not found, skipping CNN inference")
                return False
            
            # Check if CNN models exist
            model_path = self.models_dir / "pytorch_fusion_cnn" / "best_fusion_model.pth"
            scaler_path = self.povmap_backend / "output" / "fusion_pytorch_1km" / "s2_scaler_grid.pkl"
            
            if not model_path.exists():
                print(f"CNN model not found at {model_path}, skipping CNN inference")
                return False
            
            # Check for Sentinel-2 GeoTIFF
            sentinel2_tif = self.povmap_backend / "data" / "satellite_imagery" / f"sentinel2_zamboanga_{datetime.now().year}.tif"
            
            # Build command
            cmd = [
                sys.executable,
                str(cnn_script),
                "--year", str(datetime.now().year),
                "--roi_shapefile", str(self.povmap_backend / "assets" / "shapefile" / "zc04AdminBoundaries_gcs.shp"),
                "--model_path", str(model_path),
                "--scaler_path", str(scaler_path),
                "--output_dir", str(self.povmap_backend / "output" / f"cnn_reuse_1km_{datetime.now().year}"),
            ]
            
            if sentinel2_tif.exists():
                cmd.extend(["--sentinel2_tif", str(sentinel2_tif)])
            
            result = subprocess.run(
                cmd,
                cwd=str(self.povmap_backend),
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
            )
            
            if result.returncode != 0:
                print(f"CNN inference warning: {result.stderr[:500]}")
                return False
            
            # Convert CNN output to webapp format
            cnn_output_dir = self.povmap_backend / "output" / f"cnn_reuse_1km_{datetime.now().year}"
            cnn_filled_csv = cnn_output_dir / f"grid_predictions_{datetime.now().year}_filled.csv"
            
            if cnn_filled_csv.exists():
                from src.workflow.merge_predictions import create_cnn_predictions_csv
                
                webapp_cnn_csv = self.webapp_data / "all_cells_predictions_1km.csv"
                grid_gpkg = self.webapp_data / "grid_1km_all.gpkg"
                
                create_cnn_predictions_csv(
                    cnn_predictions_csv=cnn_filled_csv,
                    grid_gpkg=grid_gpkg,
                    output_csv=webapp_cnn_csv,
                )
                print(f"CNN predictions saved to {webapp_cnn_csv}")
            
            return True
            
        except Exception as e:
            print(f"CNN inference error: {str(e)}")
            traceback.print_exc()
            return False
    
    def merge_and_copy_outputs(self) -> bool:
        """
        Merge predictions and copy to webapp data directory.
        """
        self._update("MERGING", "Merging predictions...", 80)
        
        try:
            from src.workflow.merge_predictions import merge_model_predictions
            
            catboost_preds = self.povmap_backend / "output" / "catBoost" / "geospatial_disagg" / "grid_predictions.csv"
            rf_preds = self.povmap_backend / "output" / "rf" / "geospatial_disagg" / "grid_predictions.csv"
            grid_data = self.povmap_backend / "assets" / "grid_with_comprehensive_data.csv"
            raw_gee_export = self.povmap_backend / "googleEarthExports" / "zc04_grid_data_2024.csv"
            
            # Check if prediction files exist
            if not catboost_preds.exists():
                self._update("WARNING", "CatBoost predictions not found, skipping merge", 80)
                catboost_preds = None
            if not rf_preds.exists():
                self._update("WARNING", "RF predictions not found, skipping merge", 80)
                rf_preds = None
            
            if catboost_preds is None and rf_preds is None:
                self._update("ERROR", "No prediction files found to merge", 80, error="Missing predictions")
                return False
            
            if not raw_gee_export.exists():
                self._update("ERROR", "Raw GEE export not found - needed for .geo column", 80, error="Missing GEE export")
                return False
            
            output_csv = self.webapp_data / "grid_predictions_comparison.csv"
            output_geojson = self.webapp_data / "grid_with_comprehensive_data.geojson"
            comprehensive_csv = self.webapp_data / "grid_with_comprehensive_data.csv"
            
            merge_model_predictions(
                catboost_predictions_csv=catboost_preds or rf_preds,  # Use whichever exists
                rf_predictions_csv=rf_preds or catboost_preds,
                grid_data_csv=grid_data,
                raw_gee_export_csv=raw_gee_export,
                output_csv=output_csv,
                output_geojson=output_geojson,
                comprehensive_output_csv=comprehensive_csv,
            )
            
            self._update("MERGING_DONE", "Predictions merged and copied", 90)
            return True
            
        except Exception as e:
            self._update("ERROR", f"Merge error: {str(e)}", 80, error=str(e))
            traceback.print_exc()
            return False
    
    def copy_supporting_files(self) -> bool:
        """
        Copy supporting files (grid gpkg, shapefiles) to webapp data directory.
        """
        self._update("COPYING", "Copying supporting files...", 92)
        
        try:
            # Copy grid gpkg if exists
            grid_gpkg_src = self.povmap_backend / "output" / "grids" / "grid_1km.gpkg"
            grid_gpkg_dst = self.webapp_data / "grid_1km_all.gpkg"
            
            if grid_gpkg_src.exists():
                shutil.copy2(grid_gpkg_src, grid_gpkg_dst)
            
            # Copy shapefile directory if not already present
            shapefile_src = self.povmap_backend / "assets" / "shapefile"
            shapefile_dst = self.webapp_data / "shapefile"
            
            if shapefile_src.exists() and not shapefile_dst.exists():
                shutil.copytree(shapefile_src, shapefile_dst)
            
            self._update("COPYING_DONE", "Supporting files copied", 95)
            return True
            
        except Exception as e:
            self._update("WARNING", f"Copy warning: {str(e)}", 92)
            # Non-fatal error
            return True
    
    def run_full_refresh(self, skip_gee: bool = False) -> Dict[str, Any]:
        """
        Run the complete refresh pipeline.
        
        Args:
            skip_gee: If True, skip GEE extraction and use existing data
            
        Returns:
            Dict with status and any error information
        """
        start_time = datetime.now()
        self._update("STARTED", f"Starting data refresh for {self.start_date} to {self.end_date}...", 0)
        
        try:
            # Step 1: GEE Extraction
            if not skip_gee:
                if not self.run_geospatial_extraction():
                    return {"success": False, "phase": "GEE_EXTRACTION", "error": get_status().get("error")}
            else:
                self._update("GEE_SKIPPED", "Using existing GEE data", 30)
            
            # Step 2: Preprocessing
            if not self.run_preprocessing():
                return {"success": False, "phase": "PREPROCESSING", "error": get_status().get("error")}
            
            # Step 3: Model Inference
            inference_success, used_inference_module = self.run_model_inference()
            if not inference_success:
                return {"success": False, "phase": "INFERENCE", "error": get_status().get("error")}
            
            # Step 4: Merge Outputs (skip if inference module was used - it already merged)
            if not used_inference_module:
                if not self.merge_and_copy_outputs():
                    return {"success": False, "phase": "MERGING", "error": get_status().get("error")}
            else:
                print("Skipping merge step - inference module already created final output")
            
            # Step 5: Copy Supporting Files
            self.copy_supporting_files()
            
            # Done!
            elapsed = (datetime.now() - start_time).total_seconds()
            self._update(
                "COMPLETED",
                f"Refresh completed successfully in {elapsed:.1f} seconds",
                100,
                extra={"elapsed_seconds": elapsed}
            )
            
            return {
                "success": True,
                "phase": "COMPLETED",
                "elapsed_seconds": elapsed,
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as e:
            error_msg = str(e)
            self._update("ERROR", f"Refresh failed: {error_msg}", 0, error=error_msg)
            traceback.print_exc()
            return {"success": False, "phase": "ERROR", "error": error_msg}


def run_refresh_async(
    povmap_backend_dir: str,
    webapp_data_dir: str,
    models_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_gee: bool = False,
) -> Optional[threading.Thread]:
    """
    Run refresh pipeline in a background thread.
    
    Args:
        povmap_backend_dir: Path to povmapbackend directory
        webapp_data_dir: Path to webapp data directory
        models_dir: Path to models directory
        start_date: Start date for GEE extraction (YYYY-MM-DD)
        end_date: End date for GEE extraction (YYYY-MM-DD)
        skip_gee: If True, skip GEE extraction
    
    Returns:
        Thread object if started, None if already running
    """
    global _current_refresh_thread
    
    with _refresh_lock:
        if is_refresh_running():
            return None
        
        def _run():
            try:
                pipeline = RefreshPipeline(
                    povmap_backend_dir=Path(povmap_backend_dir),
                    webapp_data_dir=Path(webapp_data_dir),
                    models_dir=Path(models_dir),
                    start_date=start_date,
                    end_date=end_date,
                )
                pipeline.run_full_refresh(skip_gee=skip_gee)
            except Exception as e:
                update_status("ERROR", f"Refresh failed: {str(e)}", error=str(e))
        
        thread = threading.Thread(target=_run, daemon=True)
        _current_refresh_thread = thread
        thread.start()
        return thread


# Default paths for this project
DEFAULT_POVMAP_BACKEND = Path(r"C:\Users\Admin\povmapbackend")
DEFAULT_WEBAPP_DATA = Path(r"C:\Users\Admin\Downloads\poverty-mapping-withbackend\poverty-mapping-withbackend\data")
DEFAULT_MODELS = Path(r"C:\Users\Admin\Downloads\poverty-mapping-withbackend\poverty-mapping-withbackend\models")


def main():
    """Run refresh pipeline from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run poverty mapping data refresh pipeline")
    parser.add_argument("--povmap-backend", type=str, default=str(DEFAULT_POVMAP_BACKEND),
                       help="Path to povmapbackend directory")
    parser.add_argument("--webapp-data", type=str, default=str(DEFAULT_WEBAPP_DATA),
                       help="Path to webapp data directory")
    parser.add_argument("--models", type=str, default=str(DEFAULT_MODELS),
                       help="Path to models directory")
    parser.add_argument("--skip-gee", action="store_true",
                       help="Skip GEE extraction (use existing data)")
    args = parser.parse_args()
    
    pipeline = RefreshPipeline(
        povmap_backend_dir=Path(args.povmap_backend),
        webapp_data_dir=Path(args.webapp_data),
        models_dir=Path(args.models),
    )
    
    result = pipeline.run_full_refresh(skip_gee=args.skip_gee)
    
    if result["success"]:
        print(f"\n✓ Refresh completed successfully!")
        print(f"  Elapsed time: {result.get('elapsed_seconds', 0):.1f} seconds")
    else:
        print(f"\n✗ Refresh failed at phase: {result.get('phase')}")
        print(f"  Error: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
