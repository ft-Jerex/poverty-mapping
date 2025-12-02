# Pipeline Update Summary

## Changes Made

### 1. Fixed Geometry Format Issue
**Problem:** The preprocessing step (`preprocess_grid_data.py`) converts `.geo` (JSON format) to `geometry_wkt` (WKT format), but the webapp requires `.geo` in JSON format for map visualization.

**Solution:** Modified `merge_predictions.py` to read geometry directly from the raw GEE export (`googleEarthExports/zc04_grid_data_2024.csv`) which preserves the `.geo` column in JSON format.

### 2. Created Missing Files
**Problem:** Webapp's `app.py` requires 3 CSV files, but only 1 was being generated:
- `grid_predictions_comparison.csv` (predictions from CatBoost & RF)
- `grid_with_comprehensive_data.csv` (grid cells with geometry) ❌ MISSING
- `all_cells_predictions_1km.csv` (CNN predictions) ❌ MISSING

**Solution:** 
- `merge_predictions.py` now creates `grid_with_comprehensive_data.csv` with `.geo` column
- Added CNN inference integration to generate `all_cells_predictions_1km.csv`

### 3. Integrated CNN Pipeline
**Problem:** CNN model exists but wasn't integrated into the refresh workflow.

**Solution:** Added `run_cnn_inference()` method to `refresh_pipeline.py` that:
- Runs `cnn_data_preprocessing.py` with proper arguments
- Extracts Sentinel-2 GeoTIFF from GEE if needed
- Generates CNN predictions
- Converts output to webapp format

## Updated Files

### `src/workflow/merge_predictions.py`
- **Added parameter:** `raw_gee_export_csv` to read `.geo` column from raw GEE export
- **Added parameter:** `comprehensive_output_csv` to create `grid_with_comprehensive_data.csv`
- **Updated logic:** Now reads geometry from raw GEE export instead of preprocessed data
- **Enhanced `create_cnn_predictions_csv()`:** Better handling of various column name formats

### `src/workflow/refresh_pipeline.py`
- **Updated `merge_and_copy_outputs()`:** Passes `raw_gee_export_csv` and `comprehensive_output_csv` to merge function
- **Enhanced `run_model_inference()`:** Now calls `run_cnn_inference()` after CatBoost/RF
- **Added `run_cnn_inference()`:** New method to run CNN pipeline and convert output

## Pipeline Flow (Updated)

```
1. GEE Extraction (geospatial_prep.py)
   └─> googleEarthExports/zc04_grid_data_2024.csv [HAS .geo ✓]

2. Preprocessing (preprocess_grid_data.py)
   └─> assets/grid_with_comprehensive_data.csv [HAS geometry_wkt, barangay info]

3. Model Training/Inference
   ├─> CatBoost: output/catBoost/geospatial_disagg/grid_predictions.csv
   ├─> RF: output/rf/geospatial_disagg/grid_predictions.csv
   └─> CNN: output/cnn_reuse_1km_YYYY/grid_predictions_YYYY_filled.csv

4. Merge & Convert (merge_predictions.py)
   ├─> Reads .geo from raw GEE export (step 1)
   ├─> Reads barangay info from preprocessed data (step 2)
   ├─> Merges predictions from all models
   └─> Outputs to webapp/data/:
       ├─> grid_predictions_comparison.csv [WITH .geo ✓]
       ├─> grid_with_comprehensive_data.csv [WITH .geo ✓]
       ├─> grid_with_comprehensive_data.geojson
       └─> all_cells_predictions_1km.csv (CNN)
```

## Required Files for Webapp

| File | Location | Purpose | Status |
|------|----------|---------|--------|
| `grid_predictions_comparison.csv` | `data/` | CatBoost & RF predictions with `.geo` | ✅ Generated |
| `grid_with_comprehensive_data.csv` | `data/` | Grid cells with `.geo` and barangay info | ✅ Generated |
| `all_cells_predictions_1km.csv` | `data/` | CNN predictions | ✅ Generated |
| `grid_1km_all.gpkg` | `data/` | Grid geopackage | ✅ Copied |

## Key Data Format Requirements

### `.geo` Column (JSON Format)
```json
{"type": "Polygon", "coordinates": [[[120.123, 6.456], ...]]}
```

### `grid_predictions_comparison.csv` Columns
- `grid_id`: e.g., "0_21"
- `.geo`: JSON geometry
- `pred_scaled_catboost`: CatBoost poverty rate prediction
- `pred_scaled_rf`: Random Forest poverty rate prediction
- `barangay_name_clean`: Barangay name
- `lon`, `lat`: Coordinates

### `grid_with_comprehensive_data.csv` Columns
- `grid_id`: e.g., "0_21"
- `.geo`: JSON geometry
- `barangay_name_clean`: Barangay name
- `lon`, `lat`: Coordinates
- `x_idx`, `y_idx`: Grid indices

### `all_cells_predictions_1km.csv` Columns
- `cell_id`: Format "cell_0000_0021"
- `predicted_poverty`: CNN poverty rate prediction

## Testing Instructions

Run the complete pipeline:
```powershell
cd C:\Users\Admin\Downloads\poverty-mapping-withbackend\poverty-mapping-withbackend
python src\workflow\refresh_pipeline.py
```

Or use the web interface:
```powershell
python src\server\app.py
# Navigate to http://localhost:5000
# Click "Refresh Data" button
```

## Validation Checks

After running the pipeline, verify:

1. **Files exist:**
   ```powershell
   Test-Path data\grid_predictions_comparison.csv
   Test-Path data\grid_with_comprehensive_data.csv
   Test-Path data\all_cells_predictions_1km.csv
   ```

2. **Geometry format is correct:**
   ```powershell
   # Check for .geo column in JSON format
   Import-Csv data\grid_predictions_comparison.csv | Select-Object -First 1 | Select-Object '.geo'
   ```

3. **Map visualization works:**
   - Open webapp in browser
   - Verify map displays grid cells
   - Toggle between CatBoost, RF, and CNN models
   - Check that predictions show color-coded poverty rates

## Known Limitations

1. **CNN inference requires:**
   - Sentinel-2 GeoTIFF (will auto-download from GEE if missing)
   - Trained CNN model at `models/pytorch_fusion_cnn/best_fusion_model.pth`
   - Scaler at `output/fusion_pytorch_1km/s2_scaler_grid.pkl`
   - If any are missing, CNN predictions will be skipped (non-fatal)

2. **GEE Export filename:**
   - Currently hardcoded as `zc04_grid_data_2024.csv`
   - If you change the year in `geospatial_prep.py`, update `merge_predictions.py` accordingly

3. **Performance:**
   - Full pipeline takes 15-20 minutes
   - CNN inference adds 10-15 minutes if Sentinel-2 needs downloading

## Troubleshooting

### Map shows no predictions
- Check if `.geo` column exists: `(Import-Csv data\grid_predictions_comparison.csv).PSObject.Properties.Name | Select-String '.geo'`
- Verify geometry is JSON: `(Import-Csv data\grid_predictions_comparison.csv | Select-Object -First 1).'.geo'` should show `{"type":"Polygon",...}`

### CNN predictions missing
- Check if model exists: `Test-Path models\pytorch_fusion_cnn\best_fusion_model.pth`
- Check CNN output: `Get-ChildItem C:\Users\Admin\povmapbackend\output\cnn_reuse_1km_* -Recurse -Filter *filled.csv`
- Review pipeline logs for CNN errors

### Merge fails
- Verify raw GEE export exists: `Test-Path C:\Users\Admin\povmapbackend\googleEarthExports\zc04_grid_data_2024.csv`
- Check if it has `.geo` column: `(Import-Csv C:\Users\Admin\povmapbackend\googleEarthExports\zc04_grid_data_2024.csv).PSObject.Properties.Name | Select-String '.geo'`
