"""
End-to-end tests for the refresh inference pipeline.

Covers the full lifecycle: click refresh → API trigger → pipeline steps →
status polling → completion → data reload via /api/predictions.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

from src.workflow.refresh_pipeline import (
    RefreshPipeline,
    update_status,
    get_status,
    is_refresh_running,
    cancel_refresh,
    is_cancel_requested,
    reset_cancel_flag,
    run_refresh_async,
    STATUS_FILE,
    _current_refresh_thread,
)


# ============================================================================
# HELPERS
# ============================================================================

def _scaffold_project(tmp_path: Path) -> Path:
    """Create a minimal project tree that RefreshPipeline expects."""
    for d in ("scripts", "models", "data", "assets", "output",
              "assets/shapefile", "googleEarthExports"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Minimal preprocessed CSV so run_model_inference finds input
    (tmp_path / "data" / "grid_with_comprehensive_data.csv").write_text(
        "cell_id,lon,lat\ncell_0001_0001,122.0,7.0\n", encoding="utf-8"
    )
    return tmp_path


def _build_pipeline(tmp_path: Path, **kwargs) -> RefreshPipeline:
    root = _scaffold_project(tmp_path)
    return RefreshPipeline(project_root=root, **kwargs)


def _fake_subprocess_ok(cmd, cwd, timeout=1800):
    """Subprocess that always succeeds."""
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _fake_subprocess_fail(cmd, cwd, timeout=1800):
    """Subprocess that always fails."""
    return SimpleNamespace(returncode=1, stdout="", stderr="failure")


# ============================================================================
# 1. PIPELINE INITIALISATION
# ============================================================================

class TestPipelineInit:
    def test_valid_init(self, tmp_path):
        pipeline = _build_pipeline(tmp_path)
        assert pipeline.project_root == tmp_path
        assert pipeline.scripts_dir.exists()
        assert pipeline.models_dir.exists()

    def test_default_dates(self, tmp_path):
        pipeline = _build_pipeline(tmp_path)
        # end_date defaults to today, start_date to 365 days before
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        assert pipeline.end_date == today

    def test_custom_dates(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, start_date="2024-01-01", end_date="2024-06-30")
        assert pipeline.start_date == "2024-01-01"
        assert pipeline.end_date == "2024-06-30"

    def test_missing_scripts_dir(self, tmp_path):
        (tmp_path / "models").mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError, match="scripts"):
            RefreshPipeline(project_root=tmp_path)

    def test_missing_models_dir(self, tmp_path):
        (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
        with pytest.raises(FileNotFoundError, match="models"):
            RefreshPipeline(project_root=tmp_path)

    def test_callback_stored(self, tmp_path):
        cb = lambda msg, prog: None
        pipeline = _build_pipeline(tmp_path, callback=cb)
        assert pipeline.callback is cb


# ============================================================================
# 2. STATUS MANAGEMENT
# ============================================================================

class TestStatusManagement:
    def test_update_and_get_status(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        update_status("INFERENCE", "Running models", 55)
        status = get_status()

        assert status["phase"] == "INFERENCE"
        assert status["message"] == "Running models"
        assert status["progress"] == 55

    def test_get_status_idle_when_no_file(self, tmp_path, monkeypatch):
        status_file = tmp_path / "nonexistent" / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        status = get_status()
        assert status["phase"] == "IDLE"

    def test_update_status_with_error(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        update_status("ERROR", "Something broke", 30, error="Detailed error")
        status = get_status()

        assert status["phase"] == "ERROR"
        assert status["error"] == "Detailed error"

    def test_update_status_with_extra(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        update_status("COMPLETED", "Done", 100, extra={"elapsed_seconds": 42.5})
        status = get_status()

        assert status["elapsed_seconds"] == 42.5

    def test_is_refresh_running_idle(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", None)

        update_status("IDLE", "No refresh", 0)
        assert is_refresh_running() is False

    def test_is_refresh_running_active(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", None)

        update_status("INFERENCE", "Running", 50)
        assert is_refresh_running() is True

    def test_is_refresh_running_with_alive_thread(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", mock_thread)

        assert is_refresh_running() is True


# ============================================================================
# 3. CANCELLATION
# ============================================================================

class TestCancellation:
    def test_cancel_sets_flag(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", None)
        reset_cancel_flag()

        assert is_cancel_requested() is False
        cancel_refresh()
        assert is_cancel_requested() is True

        status = get_status()
        assert status["phase"] == "CANCELLED"

    def test_reset_cancel_flag(self):
        reset_cancel_flag()
        assert is_cancel_requested() is False

    def test_check_cancelled_in_pipeline(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        reset_cancel_flag()
        assert pipeline._check_cancelled() is False

        monkeypatch.setattr("src.workflow.refresh_pipeline._cancel_requested", True)
        assert pipeline._check_cancelled() is True

        # Cleanup
        reset_cancel_flag()


# ============================================================================
# 4. GEE EXTRACTION STEP
# ============================================================================

class TestGEEExtraction:
    def test_gee_extraction_script_missing(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        # No geospatial_prep.py → should fail gracefully
        result = pipeline.run_geospatial_extraction()
        assert result is False
        assert get_status()["phase"] == "ERROR"

    def test_gee_extraction_success(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "geospatial_prep.py").write_text(
            'start_date = "2024-01-01"\nend_date = "2024-12-31"\nprint("done")\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        assert pipeline.run_geospatial_extraction() is True
        assert get_status()["phase"] == "GEE_EXTRACTION_DONE"

    def test_gee_extraction_subprocess_fails(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "geospatial_prep.py").write_text(
            'start_date = "2024-01-01"\nend_date = "2024-12-31"\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_fail,
        )
        assert pipeline.run_geospatial_extraction() is False
        assert get_status()["phase"] == "ERROR"

    def test_gee_extraction_cleans_temp_file(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "geospatial_prep.py").write_text(
            'start_date = "2024-01-01"\nend_date = "2024-12-31"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        pipeline.run_geospatial_extraction()
        # Temp file should be cleaned up
        assert not (tmp_path / "scripts" / "_geospatial_prep_temp.py").exists()


# ============================================================================
# 5. PREPROCESSING STEP
# ============================================================================

class TestPreprocessing:
    def test_preprocessing_script_missing(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        assert pipeline.run_preprocessing() is False
        assert "ERROR" in get_status()["phase"]

    def test_preprocessing_success(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        assert pipeline.run_preprocessing() is True
        assert get_status()["phase"] == "PREPROCESSING_DONE"

    def test_preprocessing_failure(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_fail,
        )
        assert pipeline.run_preprocessing() is False


# ============================================================================
# 6. MODEL INFERENCE STEP
# ============================================================================

class TestModelInference:
    def test_missing_preprocessed_data(self, tmp_path, monkeypatch):
        """Inference should fail if preprocessed CSV is missing."""
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        root = _scaffold_project(tmp_path)
        # Remove the CSV that _scaffold_project creates
        (root / "data" / "grid_with_comprehensive_data.csv").unlink()
        (root / "assets" / "grid_with_comprehensive_data.csv").unlink(missing_ok=True)

        pipeline = RefreshPipeline(project_root=root)
        ok, used = pipeline.run_model_inference()
        assert ok is False
        assert used is False

    def test_dedicated_inference_succeeds(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        ok, used = pipeline.run_model_inference()
        assert ok is True
        assert used is True

    def test_fallback_to_training_scripts(self, tmp_path, monkeypatch):
        """If run_inference.py fails, pipeline should fall back to training scripts."""
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        scripts = tmp_path / "scripts"
        (scripts / "run_inference.py").write_text("# stub\n", encoding="utf-8")
        (scripts / "train_catboost_model.py").write_text("# stub\n", encoding="utf-8")
        (scripts / "train_rf_model.py").write_text("# stub\n", encoding="utf-8")

        calls = []

        def selective_run(cmd, cwd, timeout=1800):
            name = Path(cmd[1]).name if len(cmd) > 1 else ""
            calls.append(name)
            if name == "run_inference.py":
                return SimpleNamespace(returncode=1, stdout="", stderr="fail")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority", selective_run
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        ok, used = pipeline.run_model_inference()
        assert ok is True
        assert used is False
        assert "train_catboost_model.py" in calls
        assert "train_rf_model.py" in calls

    def test_all_inference_paths_fail(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_fail,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        ok, used = pipeline.run_model_inference()
        assert ok is False
        assert used is False

    def test_uses_assets_fallback_for_preprocessed_csv(self, tmp_path, monkeypatch):
        """If data/ CSV is missing but assets/ has it, inference should proceed."""
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        # Remove from data/, put in assets/
        (tmp_path / "data" / "grid_with_comprehensive_data.csv").unlink()
        (tmp_path / "assets" / "grid_with_comprehensive_data.csv").write_text(
            "cell_id\ncell_0001_0001\n", encoding="utf-8"
        )
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        ok, used = pipeline.run_model_inference()
        assert ok is True


# ============================================================================
# 7. CNN INFERENCE STEP
# ============================================================================

class TestCNNInference:
    def test_cnn_script_missing(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        assert pipeline.run_cnn_inference() is False

    def test_cnn_model_missing(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "cnn_data_preprocessing.py").write_text("# stub\n", encoding="utf-8")
        # No model file → should return False
        assert pipeline.run_cnn_inference() is False

    def test_cnn_subprocess_fail(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "cnn_data_preprocessing.py").write_text("# stub\n", encoding="utf-8")
        (tmp_path / "models" / "pytorch_fusion_cnn").mkdir(parents=True, exist_ok=True)
        (tmp_path / "models" / "pytorch_fusion_cnn" / "best_fusion_model.pth").write_text("dummy", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_fail,
        )
        assert pipeline.run_cnn_inference() is False


# ============================================================================
# 8. MERGE AND COPY OUTPUTS
# ============================================================================

class TestMergeAndCopy:
    def test_merge_no_predictions(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        # No prediction files → should fail
        result = pipeline.merge_and_copy_outputs()
        assert result is False

    def test_copy_supporting_files_creates_dirs(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        pipeline = _build_pipeline(tmp_path)
        # Create minimal required structure
        (tmp_path / "assets" / "shapefile").mkdir(parents=True, exist_ok=True)
        (tmp_path / "assets" / "shapefile" / "test.shp").write_text("dummy", encoding="utf-8")

        result = pipeline.copy_supporting_files()
        assert result is True
        assert (tmp_path / "data").exists()


# ============================================================================
# 9. FULL REFRESH PIPELINE (run_full_refresh)
# ============================================================================

class TestFullRefresh:
    def test_full_refresh_skip_gee_all_steps_succeed(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        pipeline = _build_pipeline(tmp_path)

        # Stub all subprocess calls to succeed
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        result = pipeline.run_full_refresh(skip_gee=True)
        assert result["success"] is True
        assert result["phase"] == "COMPLETED"
        assert "elapsed_seconds" in result

        status = get_status()
        assert status["phase"] == "COMPLETED"
        assert status["progress"] == 100

    def test_full_refresh_cancelled_before_start(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        # Set cancel flag before running
        monkeypatch.setattr("src.workflow.refresh_pipeline._cancel_requested", True)

        pipeline = _build_pipeline(tmp_path)
        result = pipeline.run_full_refresh(skip_gee=True)

        assert result["success"] is False
        assert result["phase"] == "CANCELLED"

        # Cleanup
        reset_cancel_flag()

    def test_full_refresh_gee_failure_stops_pipeline(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        pipeline = _build_pipeline(tmp_path)
        # No geospatial_prep.py → GEE step will fail
        result = pipeline.run_full_refresh(skip_gee=False)

        assert result["success"] is False
        assert result["phase"] == "GEE_EXTRACTION"

    def test_full_refresh_preprocessing_failure_stops_pipeline(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        pipeline = _build_pipeline(tmp_path)
        # No preprocess script → will fail
        result = pipeline.run_full_refresh(skip_gee=True)

        assert result["success"] is False
        assert result["phase"] == "PREPROCESSING"

    def test_full_refresh_inference_failure_stops_pipeline(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")

        # Preprocessing OK, but inference fails (no scripts, no preprocessed data)
        (tmp_path / "data" / "grid_with_comprehensive_data.csv").unlink()

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )

        result = pipeline.run_full_refresh(skip_gee=True)
        assert result["success"] is False
        assert result["phase"] == "INFERENCE"

    def test_full_refresh_cancellation_mid_pipeline(self, tmp_path, monkeypatch):
        """Cancellation between steps should stop the pipeline."""
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        call_count = 0

        def subprocess_that_cancels(cmd, cwd, timeout=1800):
            nonlocal call_count
            call_count += 1
            # After preprocessing succeeds, set cancel flag
            name = Path(cmd[1]).name if len(cmd) > 1 else ""
            if name == "preprocess_grid_data.py":
                import src.workflow.refresh_pipeline as rp
                rp._cancel_requested = True
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            subprocess_that_cancels,
        )

        result = pipeline.run_full_refresh(skip_gee=True)
        assert result["success"] is False
        assert result["phase"] == "CANCELLED"

        reset_cancel_flag()

    def test_callback_invoked_during_refresh(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        messages = []

        def track_callback(message, progress):
            messages.append((message, progress))

        pipeline = _build_pipeline(tmp_path, callback=track_callback)
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        pipeline.run_full_refresh(skip_gee=True)
        assert len(messages) > 0
        # Final callback should be completion message
        assert any("complete" in m[0].lower() or "complete" in m[0] for m in messages)


# ============================================================================
# 10. ASYNC REFRESH (run_refresh_async)
# ============================================================================

class TestAsyncRefresh:
    def test_run_refresh_async_returns_thread(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", None)
        reset_cancel_flag()

        # Stub subprocess and CNN
        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        # Create project structure
        root = _scaffold_project(tmp_path)
        (root / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        (root / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        thread = run_refresh_async(
            project_root=str(root),
            start_date="2024-01-01",
            end_date="2024-06-30",
            skip_gee=True,
        )
        assert thread is not None
        thread.join(timeout=30)
        assert not thread.is_alive()

        status = get_status()
        assert status["phase"] == "COMPLETED"

    def test_run_refresh_async_blocks_concurrent(self, tmp_path, monkeypatch):
        """Second call while first is running should return None."""
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        # Make the pipeline hang a bit
        import time as _time

        original_ok = _fake_subprocess_ok

        def slow_subprocess(cmd, cwd, timeout=1800):
            _time.sleep(0.5)
            return original_ok(cmd, cwd, timeout)

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            slow_subprocess,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        root = _scaffold_project(tmp_path)
        (root / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        (root / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        thread1 = run_refresh_async(project_root=str(root), skip_gee=True)
        assert thread1 is not None

        # Try to start another immediately
        thread2 = run_refresh_async(project_root=str(root), skip_gee=True)
        assert thread2 is None

        thread1.join(timeout=30)


# ============================================================================
# 11. FLASK API ENDPOINTS
# ============================================================================

class TestFlaskAPIEndpoints:
    """Test the server-side refresh endpoints using Flask test client."""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        """Create a Flask test client with isolated database."""
        # Point the database to tmp_path
        db_path = tmp_path / "users.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        status_file = data_dir / "refresh_status.json"

        monkeypatch.setattr("src.server.app.USERS_DB_PATH", db_path)
        monkeypatch.setattr("src.server.app.DATA_DIR", data_dir)
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        from src.server.app import app, _init_users_db
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret"

        # Re-init DB in tmp location
        _init_users_db()

        with app.test_client() as c:
            yield c

    def _login(self, client, username="testadmin", password="testpass"):
        """Register and login a test admin."""
        from src.server.app import _get_db_connection
        from werkzeug.security import generate_password_hash

        conn = _get_db_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            conn.commit()
        finally:
            conn.close()

        client.post(
            "/auth/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def test_refresh_status_unauthenticated(self, client):
        """Status endpoint should work without auth (always returns valid JSON)."""
        res = client.get("/api/refresh/status")
        assert res.status_code == 200
        data = res.get_json()
        assert "phase" in data

    def test_refresh_trigger_unauthenticated(self, client):
        """Triggering refresh without login should return 401."""
        res = client.post(
            "/api/refresh",
            json={"start_date": "2024-01-01", "end_date": "2024-06-30"},
        )
        assert res.status_code == 401

    def test_refresh_cancel_unauthenticated(self, client):
        """Cancelling without login should return 401."""
        res = client.post("/api/refresh/cancel")
        assert res.status_code == 401

    def test_refresh_check_unauthenticated(self, client):
        """Check endpoint without login should return 401."""
        res = client.get("/api/refresh/check")
        assert res.status_code == 401

    def test_refresh_trigger_invalid_dates(self, client, monkeypatch):
        """Invalid date format should return 400."""
        self._login(client)
        res = client.post(
            "/api/refresh",
            json={"start_date": "not-a-date", "end_date": "2024-06-30"},
        )
        assert res.status_code == 400
        assert "Invalid" in res.get_json()["error"]

    def test_refresh_trigger_start_after_end(self, client, monkeypatch):
        """start_date >= end_date should return 400."""
        self._login(client)
        res = client.post(
            "/api/refresh",
            json={"start_date": "2024-12-01", "end_date": "2024-01-01"},
        )
        assert res.status_code == 400
        assert "before" in res.get_json()["error"]

    def test_refresh_trigger_date_range_too_large(self, client, monkeypatch):
        """Date range > 365 days should return 400."""
        self._login(client)
        res = client.post(
            "/api/refresh",
            json={"start_date": "2022-01-01", "end_date": "2024-01-01"},
        )
        assert res.status_code == 400
        assert "exceed" in res.get_json()["error"]

    def test_refresh_trigger_success(self, client, tmp_path, monkeypatch):
        """Successful refresh trigger should return 200 with success=True."""
        self._login(client)
        reset_cancel_flag()
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", None)

        root = _scaffold_project(tmp_path / "project")
        monkeypatch.setattr("src.server.app.ROOT", root)

        # Stub the refresh pipeline to complete quickly
        def mock_run_refresh_async(project_root, start_date=None, end_date=None, skip_gee=False):
            update_status("COMPLETED", "Done", 100)
            t = threading.Thread(target=lambda: None)
            t.start()
            return t

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline.run_refresh_async",
            mock_run_refresh_async,
        )
        monkeypatch.setattr(
            "src.workflow.refresh_pipeline.is_refresh_running",
            lambda: False,
        )

        res = client.post(
            "/api/refresh",
            json={
                "start_date": "2024-01-01",
                "end_date": "2024-06-30",
                "force": True,
            },
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "refresh_id" in data

    def test_refresh_status_returns_current_phase(self, client, tmp_path, monkeypatch):
        """Status endpoint should reflect the current pipeline phase."""
        status_file = tmp_path / "data" / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)

        update_status("INFERENCE", "Running models", 55)

        res = client.get("/api/refresh/status")
        assert res.status_code == 200
        data = res.get_json()
        assert data["phase"] == "INFERENCE"
        assert data["progress"] == 55

    def test_refresh_cancel_no_running_refresh(self, client, tmp_path, monkeypatch):
        """Cancel when nothing is running should indicate no refresh."""
        self._login(client)
        status_file = tmp_path / "data" / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        monkeypatch.setattr("src.workflow.refresh_pipeline._current_refresh_thread", None)

        update_status("IDLE", "No refresh", 0)

        res = client.post("/api/refresh/cancel")
        data = res.get_json()
        assert data["success"] is False
        assert "No refresh" in data["error"]

    def test_refresh_history_empty(self, client):
        """History should return empty list when no refreshes recorded."""
        self._login(client)
        res = client.get("/api/refresh/history")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["history"] == []


# ============================================================================
# 12. STATUS FILE PHASE PROGRESSION
# ============================================================================

class TestPhaseProgression:
    """Verify that the pipeline progresses through expected phases."""

    def test_phase_sequence_skip_gee(self, tmp_path, monkeypatch):
        status_file = tmp_path / "refresh_status.json"
        monkeypatch.setattr("src.workflow.refresh_pipeline.STATUS_FILE", status_file)
        reset_cancel_flag()

        phases = []
        original_update = update_status

        def tracking_update(phase, message="", progress=0, total_steps=100,
                            error=None, extra=None):
            phases.append(phase)
            original_update(phase, message, progress, total_steps, error, extra)

        monkeypatch.setattr("src.workflow.refresh_pipeline.update_status", tracking_update)

        pipeline = _build_pipeline(tmp_path)
        (tmp_path / "scripts" / "preprocess_grid_data.py").write_text("# stub\n", encoding="utf-8")
        (tmp_path / "scripts" / "run_inference.py").write_text("# stub\n", encoding="utf-8")

        monkeypatch.setattr(
            "src.workflow.refresh_pipeline._run_subprocess_low_priority",
            _fake_subprocess_ok,
        )
        monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

        result = pipeline.run_full_refresh(skip_gee=True)
        assert result["success"] is True

        # Verify key phases appeared in order
        assert "STARTED" in phases
        assert "GEE_SKIPPED" in phases
        assert "PREPROCESSING" in phases
        assert "INFERENCE" in phases
        assert "COMPLETED" in phases

        # STARTED should come before COMPLETED
        assert phases.index("STARTED") < phases.index("COMPLETED")


# ============================================================================
# 13. DATA RELOAD AFTER REFRESH (/api/predictions cache invalidation)
# ============================================================================

class TestDataReloadAfterRefresh:
    """Test that after refresh, /api/predictions returns updated data.
    
    NOTE: get_data() uses _data_cache. The first call populates it.
    For the app to show new data after refresh, the cache must be
    invalidated. This test documents/verifies the behaviour.
    """

    def test_data_cache_returns_same_object(self):
        """Verify that get_data() returns cached result on second call."""
        from src.server.app import get_data, _data_cache
        import src.server.app as app_module

        # Save original
        original_cache = app_module._data_cache

        try:
            # Set a known cache value
            app_module._data_cache = {"test": True}
            result = get_data()
            assert result == {"test": True}
        finally:
            app_module._data_cache = original_cache

    def test_predictions_endpoint_returns_json(self, tmp_path, monkeypatch):
        """Verify /api/predictions returns well-formed JSON with model keys."""
        import src.server.app as app_module

        original_cache = app_module._data_cache
        try:
            # Inject mock data
            app_module._data_cache = {
                "boundary": None,
                "barangayLabels": None,
                "models": {
                    "catboost": {"type": "FeatureCollection", "features": []},
                    "rf": {"type": "FeatureCollection", "features": []},
                    "cnn": None,
                },
                "censusPoverty": None,
                "quartileRanges": {},
            }

            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as client:
                res = client.get("/api/predictions")
                assert res.status_code == 200
                data = res.get_json()
                assert "models" in data
                assert "catboost" in data["models"]
                assert "rf" in data["models"]
        finally:
            app_module._data_cache = original_cache


# ============================================================================
# 14. MERGE PREDICTIONS MODULE
# ============================================================================

class TestMergePredictions:
    def test_fill_missing_predictions_spatial(self):
        """Spatial interpolation should fill NaN prediction values."""
        import pandas as pd
        from src.workflow.merge_predictions import fill_missing_predictions_spatial

        df = pd.DataFrame({
            "lon": [122.0, 122.01, 122.02, 122.03],
            "lat": [7.0, 7.01, 7.02, 7.03],
            "pred_col": [0.3, None, 0.5, None],
        })
        result = fill_missing_predictions_spatial(df, "pred_col", k_neighbors=2)
        assert result["pred_col"].isna().sum() == 0

    def test_fill_missing_no_missing(self):
        """Should return unchanged DataFrame when no values are missing."""
        import pandas as pd
        from src.workflow.merge_predictions import fill_missing_predictions_spatial

        df = pd.DataFrame({
            "lon": [122.0, 122.01],
            "lat": [7.0, 7.01],
            "pred_col": [0.3, 0.5],
        })
        result = fill_missing_predictions_spatial(df, "pred_col")
        assert result["pred_col"].tolist() == [0.3, 0.5]

    def test_fill_missing_no_lonlat(self):
        """Should return unchanged when lon/lat are missing."""
        import pandas as pd
        from src.workflow.merge_predictions import fill_missing_predictions_spatial

        df = pd.DataFrame({"pred_col": [0.3, None]})
        result = fill_missing_predictions_spatial(df, "pred_col")
        # Can't interpolate without coordinates, should remain as-is
        assert result["pred_col"].isna().sum() == 1

    def test_create_cnn_predictions_csv(self, tmp_path):
        """Should convert grid-format predictions to cell_id format."""
        import pandas as pd
        from src.workflow.merge_predictions import create_cnn_predictions_csv

        input_csv = tmp_path / "cnn_input.csv"
        pd.DataFrame({
            "grid_id": ["0_21", "1_5"],
            "predicted_poverty": [0.35, 0.62],
        }).to_csv(input_csv, index=False)

        output_csv = tmp_path / "cnn_output.csv"
        result = create_cnn_predictions_csv(input_csv, tmp_path / "grid.gpkg", output_csv)

        assert output_csv.exists()
        assert "cell_id" in result.columns
        assert "predicted_poverty" in result.columns
        assert len(result) == 2
        # Verify cell_id format conversion
        assert result.iloc[0]["cell_id"] == "cell_0000_0021"
        assert result.iloc[1]["cell_id"] == "cell_0001_0005"


# ============================================================================
# 15. INFERENCE MODULE UNIT TESTS
# ============================================================================

class TestInferenceModule:
    def test_spatial_knn_impute_fills_na(self):
        """KNN imputation should fill missing feature values."""
        import pandas as pd
        from src.model.inference import spatial_knn_impute_features

        df = pd.DataFrame({
            "lon": [122.0, 122.01, 122.02, 122.03, 122.04],
            "lat": [7.0, 7.01, 7.02, 7.03, 7.04],
            "elevation": [100.0, None, 120.0, None, 110.0],
        })
        result = spatial_knn_impute_features(df, ["elevation"], k_neighbors=3)
        assert result["elevation"].isna().sum() == 0

    def test_spatial_knn_impute_no_coords(self):
        """Without lon/lat, should fallback to median fill."""
        import pandas as pd
        from src.model.inference import spatial_knn_impute_features

        df = pd.DataFrame({"elevation": [100.0, None, 120.0]})
        result = spatial_knn_impute_features(df, ["elevation"])
        assert result["elevation"].isna().sum() == 0
        assert result["elevation"].iloc[1] == 110.0  # median of 100, 120

    def test_add_multiscale_aggregates(self):
        """Should add neighborhood mean/std columns."""
        import pandas as pd
        from src.model.inference import add_multiscale_aggregates

        df = pd.DataFrame({
            "lon": [122.0, 122.001, 122.002],
            "lat": [7.0, 7.001, 7.002],
            "elevation": [100.0, 110.0, 120.0],
        })
        result_df, new_cols = add_multiscale_aggregates(df, ["elevation"], [500])
        assert "elevation_nb_mean_r500" in new_cols
        assert "elevation_nb_std_r500" in new_cols
        assert len(new_cols) == 2

    def test_add_within_barangay_features_no_barangay(self):
        """Without barangay column, deviation features should be zero."""
        import pandas as pd
        from src.model.inference import add_within_barangay_features

        df = pd.DataFrame({"elevation": [100.0, 110.0, 120.0]})
        result_df, new_cols = add_within_barangay_features(df, ["elevation"])
        assert "elevation_brgy_zscore" in new_cols
        assert "elevation_brgy_dev" in new_cols
        assert (result_df["elevation_brgy_zscore"] == 0.0).all()
        assert (result_df["elevation_brgy_dev"] == 0.0).all()

    def test_add_within_barangay_features_with_barangay(self):
        """With barangay column, should compute z-scores and deviations."""
        import pandas as pd
        from src.model.inference import add_within_barangay_features

        df = pd.DataFrame({
            "barangay_name_clean": ["A", "A", "B", "B"],
            "elevation": [100.0, 120.0, 200.0, 220.0],
        })
        result_df, new_cols = add_within_barangay_features(df, ["elevation"])
        assert "elevation_brgy_dev" in new_cols
        # Deviation of first row: 100 - mean(100,120) = 100 - 110 = -10
        assert abs(result_df["elevation_brgy_dev"].iloc[0] - (-10.0)) < 1e-6

    def test_load_feature_columns(self, tmp_path):
        """Should load JSON array of feature column names."""
        import json
        from src.model.inference import load_feature_columns

        features = ["feat_a", "feat_b", "feat_c"]
        path = tmp_path / "features.json"
        path.write_text(json.dumps(features), encoding="utf-8")

        loaded = load_feature_columns(path)
        assert loaded == features
