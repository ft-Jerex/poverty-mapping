from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.workflow.refresh_pipeline import RefreshPipeline


def _build_pipeline(tmp_path: Path) -> RefreshPipeline:
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "models").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "output").mkdir(parents=True)

    # Minimal input marker so run_model_inference finds preprocessed data.
    (tmp_path / "data" / "grid_with_comprehensive_data.csv").write_text("cell_id\ncell_0001_0001\n", encoding="utf-8")

    return RefreshPipeline(project_root=tmp_path)


def test_run_model_inference_falls_back_when_dedicated_inference_fails(tmp_path: Path, monkeypatch):
    pipeline = _build_pipeline(tmp_path)

    scripts_dir = tmp_path / "scripts"
    (scripts_dir / "run_inference.py").write_text("# stub\n", encoding="utf-8")
    (scripts_dir / "train_catboost_model.py").write_text("# stub\n", encoding="utf-8")
    (scripts_dir / "train_rf_model.py").write_text("# stub\n", encoding="utf-8")

    calls: list[str] = []

    def fake_run(cmd, cwd, timeout=1800):
        script_name = Path(cmd[1]).name if len(cmd) > 1 else ""
        calls.append(script_name)
        if script_name == "run_inference.py":
            return SimpleNamespace(returncode=1, stdout="", stderr="inference failed")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("src.workflow.refresh_pipeline._run_subprocess_low_priority", fake_run)
    monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

    ok, used_inference_module = pipeline.run_model_inference()

    assert ok is True
    assert used_inference_module is False
    assert "run_inference.py" in calls
    assert "train_catboost_model.py" in calls
    assert "train_rf_model.py" in calls


def test_run_model_inference_fails_when_no_prediction_path_succeeds(tmp_path: Path, monkeypatch):
    pipeline = _build_pipeline(tmp_path)

    scripts_dir = tmp_path / "scripts"
    (scripts_dir / "run_inference.py").write_text("# stub\n", encoding="utf-8")

    def fake_run(cmd, cwd, timeout=1800):
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    monkeypatch.setattr("src.workflow.refresh_pipeline._run_subprocess_low_priority", fake_run)
    monkeypatch.setattr(RefreshPipeline, "run_cnn_inference", lambda self: True)

    ok, used_inference_module = pipeline.run_model_inference()

    assert ok is False
    assert used_inference_module is False
