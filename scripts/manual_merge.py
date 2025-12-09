"""
Manual merge script to generate the required CSV files with proper .geo column.
Run this to fix the missing files issue.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from workflow.merge_predictions import merge_model_predictions

# Use project-relative paths (all files are now inside this workspace)
project_root = Path(__file__).parent
output = project_root / "data"

print("Starting merge with updated parameters...")
print(f"Project root: {project_root}")
print(f"Output: {output}")

try:
    # Merge CatBoost and RF predictions with .geo column from raw GEE export
    # Uses grid_1km_all.gpkg as authoritative grid source for full ROI coverage
    result = merge_model_predictions(
        catboost_predictions_csv=project_root / "output" / "catBoost" / "geospatial_disagg" / "grid_predictions.csv",
        rf_predictions_csv=project_root / "output" / "rf" / "geospatial_disagg" / "grid_predictions.csv",
        grid_data_csv=project_root / "assets" / "grid_with_comprehensive_data.csv",
        raw_gee_export_csv=project_root / "googleEarthExports" / "zc04_grid_data_2024.csv",
        output_csv=output / "grid_predictions_comparison.csv",
        output_geojson=output / "grid_with_comprehensive_data.geojson",
        comprehensive_output_csv=output / "grid_with_comprehensive_data.csv",
        grid_gpkg_path=output / "grid_1km_all.gpkg",  # Authoritative grid for full ROI
    )
    print("\n✓ Merge completed successfully!")
    print(f"Total grid cells: {len(result)}")
    print(f"Generated files:")
    print(f"  - {output / 'grid_predictions_comparison.csv'}")
    print(f"  - {output / 'grid_with_comprehensive_data.csv'}")
    print(f"  - {output / 'grid_with_comprehensive_data.geojson'}")
    
except Exception as e:
    print(f"\n✗ Error during merge: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
