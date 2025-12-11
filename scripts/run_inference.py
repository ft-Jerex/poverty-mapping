#!/usr/bin/env python3
"""
Standalone script to run model inference as a subprocess.
This prevents blocking the main Flask/gunicorn process.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="Run model inference")
    parser.add_argument("--preprocessed-csv", type=str, required=True,
                        help="Path to preprocessed grid data CSV")
    parser.add_argument("--models-dir", type=str, required=True,
                        help="Path to models directory")
    parser.add_argument("--output-dir", type=str, required=True,
                        help="Path to output directory")
    parser.add_argument("--project-root", type=str, default=str(PROJECT_ROOT),
                        help="Project root directory")
    args = parser.parse_args()
    
    preprocessed_csv = Path(args.preprocessed_csv)
    models_dir = Path(args.models_dir)
    output_dir = Path(args.output_dir)
    project_root = Path(args.project_root)
    
    if not preprocessed_csv.exists():
        print(f"ERROR: Preprocessed CSV not found: {preprocessed_csv}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Running inference with:")
    print(f"  Preprocessed CSV: {preprocessed_csv}")
    print(f"  Models dir: {models_dir}")
    print(f"  Output dir: {output_dir}")
    
    try:
        from src.model.inference import run_all_models
        
        outputs = run_all_models(
            preprocessed_csv=preprocessed_csv,
            models_dir=models_dir,
            output_dir=output_dir,
            povmap_backend_dir=project_root,
        )
        
        print(f"Inference complete. Generated {len(outputs)} outputs:")
        for name, path in outputs.items():
            print(f"  {name}: {path}")
        
        sys.exit(0)
        
    except Exception as e:
        print(f"ERROR: Inference failed: {str(e)}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
