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
import os
import platform
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


def _run_subprocess_low_priority(cmd, cwd, timeout=1800):
    """
    Run a subprocess with low CPU/IO priority to prevent site lag.
    Uses nice on Linux to reduce priority.
    """
    if platform.system() == "Linux":
        # Use nice to lower CPU priority (19 = lowest)
        # ionice -c 3 = idle IO class (only use IO when system is idle)
        nice_cmd = ["nice", "-n", "19"] + cmd
    else:
        nice_cmd = cmd
    
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    
    result = subprocess.run(
        nice_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        env=env
    )
    return result


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
        # Ensure directory exists
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
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
        project_root: Path,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        callback: Optional[Callable[[str, int], None]] = None,
    ):
        """
        Initialize the refresh pipeline.
        
        Args:
            project_root: Path to project root (poverty-mapping-withbackend)
            start_date: Start date for GEE data extraction (YYYY-MM-DD), default 1 year ago
            end_date: End date for GEE data extraction (YYYY-MM-DD), default today
            callback: Optional callback function(message, progress) for status updates
        """
        self.project_root = Path(project_root)
        self.scripts_dir = self.project_root / "scripts"
        self.assets_dir = self.project_root / "assets"
        self.output_dir = self.project_root / "output"
        self.gee_exports_dir = self.project_root / "googleEarthExports"
        self.webapp_data = self.project_root / "data"
        self.models_dir = self.project_root / "models"
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
        
        # Verify required paths exist
        if not self.scripts_dir.exists():
            raise FileNotFoundError(f"scripts/ not found at {self.scripts_dir}")
        if not self.models_dir.exists():
            raise FileNotFoundError(f"models/ not found at {self.models_dir}")
    
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
        
        script_path = self.scripts_dir / "geospatial_prep.py"
        
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
            temp_script = self.scripts_dir / "_geospatial_prep_temp.py"
            temp_script.write_text(modified_content, encoding='utf-8')
            
            try:
                # Run the modified script with low priority to prevent site lag
                self._update("GEE_EXTRACTION", "Step 1/5: Connecting to Google Earth Engine...", 5)
                result = _run_subprocess_low_priority(
                    [sys.executable, "-X", "utf8", str(temp_script)],
                    cwd=self.project_root,
                    timeout=1800  # 30 minute timeout
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
    
    def run_preprocessing(self) -> bool:
        """
        Run grid data preprocessing.
        
        This calls preprocess_grid_data.py to attach barangay info.
        """
        self._update("PREPROCESSING", "Step 2/5: Processing grid data and attaching barangay info...", 35)
        
        script_path = self.scripts_dir / "preprocess_grid_data.py"
        
        if not script_path.exists():
            self._update("ERROR", f"preprocess_grid_data.py not found", 35, error="Script not found")
            return False
        
        try:
            result = _run_subprocess_low_priority(
                [sys.executable, str(script_path)],
                cwd=self.project_root,
                timeout=600  # 10 minute timeout
            )
            
            if result.returncode != 0:
                self._update(
                    "ERROR",
                    f"Preprocessing failed: {result.stderr[:500]}",
                    35,
                    error=result.stderr[:1000]
                )
                return False
            
            self._update("PREPROCESSING_DONE", "Step 2/5: Grid preprocessing complete", 50)
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
        self._update("INFERENCE", "Step 3/5: Running poverty prediction models...", 55)
        
        try:
            from src.model.inference import run_all_models
            
            # Use the authoritative, latest comprehensive grid data produced by
            # the pipeline for the webapp (data/grid_with_comprehensive_data.csv),
            # rather than the older snapshot in assets/.
            preprocessed_csv = self.webapp_data / "grid_with_comprehensive_data.csv"
            
            if not preprocessed_csv.exists():
                self._update("ERROR", "Preprocessed data not found", 55, error="Missing input file")
                return False, False
            
            # Write raw model outputs to the standard output directory; downstream
            # merge_and_copy_outputs will consume these and create web-ready files.
            output_dir = self.output_dir
            
            outputs = run_all_models(
                preprocessed_csv=preprocessed_csv,
                models_dir=self.models_dir,
                output_dir=output_dir,
                povmap_backend_dir=self.project_root,
            )
            
            self._update(
                "INFERENCE_DONE",
                f"Model inference complete. Generated {len(outputs)} outputs.",
                70,
                extra={"outputs": [str(p) for p in outputs.values()]}
            )
            
            # Run CNN inference if models exist
            self.run_cnn_inference()
            
            # We still rely on merge_and_copy_outputs to perform the geometry-aware
            # merge and webapp-specific file creation, so mark used_inference_module
            # as False to ensure that step is executed.
            return True, False
            
        except ImportError:
            # Fallback: run training scripts which also save predictions
            self._update("INFERENCE", "Step 3/5: Running CatBoost model training...", 55)
            
            try:
                # Run CatBoost with low priority
                catboost_script = self.scripts_dir / "train_catboost_model.py"
                if catboost_script.exists():
                    result = _run_subprocess_low_priority(
                        [sys.executable, str(catboost_script)],
                        cwd=self.project_root,
                        timeout=1200
                    )
                    if result.returncode != 0:
                        print(f"CatBoost warning: {result.stderr[:500]}")
                
                self._update("INFERENCE", "Step 3/5: Running Random Forest model training...", 62)
                
                # Run RF with low priority
                rf_script = self.scripts_dir / "train_rf_model.py"
                if rf_script.exists():
                    result = _run_subprocess_low_priority(
                        [sys.executable, str(rf_script)],
                        cwd=self.project_root,
                        timeout=1200
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
            cnn_script = self.scripts_dir / "cnn_data_preprocessing.py"
            if not cnn_script.exists():
                print("CNN script not found, skipping CNN inference")
                return False
            
            # Check if CNN models exist
            model_path = self.models_dir / "pytorch_fusion_cnn" / "best_fusion_model.pth"
            scaler_path = self.output_dir / "fusion_pytorch_1km" / "s2_scaler_grid.pkl"
            
            if not model_path.exists():
                print(f"CNN model not found at {model_path}, skipping CNN inference")
                return False
            
            # Check for Sentinel-2 GeoTIFF
            sentinel2_tif = self.project_root / "data" / "satellite_imagery" / f"sentinel2_zamboanga_{datetime.now().year}.tif"
            
            # Build command
            cmd = [
                sys.executable,
                str(cnn_script),
                "--year", str(datetime.now().year),
                "--roi_shapefile", str(self.assets_dir / "shapefile" / "zc04AdminBoundaries_gcs.shp"),
                "--model_path", str(model_path),
                "--scaler_path", str(scaler_path),
                "--output_dir", str(self.output_dir / f"cnn_reuse_1km_{datetime.now().year}"),
            ]
            
            if sentinel2_tif.exists():
                cmd.extend(["--sentinel2_tif", str(sentinel2_tif)])
            
            result = _run_subprocess_low_priority(
                cmd,
                cwd=self.project_root,
                timeout=3600  # 1 hour timeout
            )
            
            if result.returncode != 0:
                print(f"CNN inference warning: {result.stderr[:500]}")
                return False
            
            # Convert CNN output to webapp format
            cnn_output_dir = self.output_dir / f"cnn_reuse_1km_{datetime.now().year}"
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
        self._update("MERGING", "Step 4/5: Merging predictions and generating outputs...", 80)
        
        try:
            from src.workflow.merge_predictions import merge_model_predictions
            
            catboost_preds = self.output_dir / "catBoost" / "geospatial_disagg" / "grid_predictions.csv"
            rf_preds = self.output_dir / "rf" / "geospatial_disagg" / "grid_predictions.csv"
            grid_data = self.assets_dir / "grid_with_comprehensive_data.csv"
            # Select latest available GEE export CSV (avoid hardcoded year)
            gee_dir = self.gee_exports_dir
            raw_gee_export = gee_dir / "zc04_grid_data_2024.csv"
            if gee_dir.exists():
                try:
                    candidates = sorted(gee_dir.glob("zc04_grid_data_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if candidates:
                        raw_gee_export = candidates[0]
                        print(f"Using latest GEE export: {raw_gee_export}")
                except Exception:
                    pass
            
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
                # Fallback: continue merge without .geo; frontend will still show predictions
                self._update("WARNING", "Raw GEE export not found - proceeding without .geo geometry", 80)
            
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
            
            self._update("MERGING_DONE", "Step 4/5: Predictions merged successfully", 90)
            return True
            
        except Exception as e:
            self._update("ERROR", f"Merge error: {str(e)}", 80, error=str(e))
            traceback.print_exc()
            return False
    
    def copy_supporting_files(self) -> bool:
        """
        Copy supporting files (grid gpkg, shapefiles) to webapp data directory.
        """
        self._update("COPYING", "Step 5/5: Finalizing and saving results...", 92)
        
        try:
            # Copy grid gpkg if exists
            grid_gpkg_src = self.output_dir / "grids" / "grid_1km.gpkg"
            grid_gpkg_dst = self.webapp_data / "grid_1km_all.gpkg"
            
            if grid_gpkg_src.exists():
                shutil.copy2(grid_gpkg_src, grid_gpkg_dst)
            
            # Copy shapefile directory if not already present
            shapefile_src = self.assets_dir / "shapefile"
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
            minutes = int(elapsed // 60)
            seconds = int(elapsed % 60)
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            self._update(
                "COMPLETED",
                f"✓ Refresh complete! All predictions updated. (Time: {time_str})",
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
    project_root: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_gee: bool = False,
) -> Optional[threading.Thread]:
    """
    Run refresh pipeline in a background thread.
    
    Args:
        project_root: Path to project root directory (poverty-mapping-withbackend)
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
                    project_root=Path(project_root),
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


# Default paths - all inside this workspace
_PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"      # GEE extraction, preprocessing, training scripts
DEFAULT_ASSETS_DIR = _PROJECT_ROOT / "assets"        # Shapefiles, socioeconomic CSVs
DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "output"        # Model outputs
DEFAULT_GEE_EXPORTS_DIR = _PROJECT_ROOT / "googleEarthExports"
DEFAULT_WEBAPP_DATA = _PROJECT_ROOT / "data"
DEFAULT_MODELS = _PROJECT_ROOT / "models"


def main():
    """Run refresh pipeline from command line."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run poverty mapping data refresh pipeline")
    parser.add_argument("--project-root", type=str, default=str(_PROJECT_ROOT),
                       help="Path to project root (poverty-mapping-withbackend)")
    parser.add_argument("--skip-gee", action="store_true",
                       help="Skip GEE extraction (use existing data)")
    args = parser.parse_args()
    
    pipeline = RefreshPipeline(
        project_root=Path(args.project_root),
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
