"""
Manual merge script to generate the required CSV files with proper .geo column.
Run this to fix the missing files issue.
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from workflow.merge_predictions import merge_model_predictions

backend = Path(r"C:\Users\Admin\povmapbackend")
output = Path(__file__).parent / "data"

print("Starting merge with updated parameters...")
print(f"Backend: {backend}")
print(f"Output: {output}")

try:
    # Merge CatBoost and RF predictions with .geo column from raw GEE export
    result = merge_model_predictions(
        catboost_predictions_csv=backend / "output" / "catBoost" / "geospatial_disagg" / "grid_predictions.csv",
        rf_predictions_csv=backend / "output" / "rf" / "geospatial_disagg" / "grid_predictions.csv",
        grid_data_csv=backend / "assets" / "grid_with_comprehensive_data.csv",
        raw_gee_export_csv=backend / "googleEarthExports" / "zc04_grid_data_2024.csv",
        output_csv=output / "grid_predictions_comparison.csv",
        output_geojson=output / "grid_with_comprehensive_data.geojson",
        comprehensive_output_csv=output / "grid_with_comprehensive_data.csv",
    )
    print("\n✓ Merge completed successfully!")
    print(f"Generated files:")
    print(f"  - {output / 'grid_predictions_comparison.csv'}")
    print(f"  - {output / 'grid_with_comprehensive_data.csv'}")
    print(f"  - {output / 'grid_with_comprehensive_data.geojson'}")
    
except Exception as e:
    print(f"\n✗ Error during merge: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
