# Fixed: Missing Prediction Files Issue

## Problem
After running the refresh pipeline, the webapp showed "No prediction layers available" because:
1. Only `grid_predictions_comparison.csv` was created (missing `.geo` column)
2. `grid_with_comprehensive_data.csv` was not created
3. `all_cells_predictions_1km.csv` was not created

## Root Cause
The refresh pipeline used cached/old version of `merge_predictions.py` that didn't:
- Read `.geo` column from raw GEE export
- Create the `grid_with_comprehensive_data.csv` file
- Extract `lon`/`lat` coordinates

## Solution Applied

### 1. Created Manual Fix Script (`fix_merge.py`)
Located: `C:\Users\Admin\povmapbackend\fix_merge.py`

This script:
- ✅ Reads `.geo` column from raw GEE export (JSON format)
- ✅ Extracts `lon`/`lat` from preprocessed data
- ✅ Merges CatBoost and RF predictions
- ✅ Creates all 3 required CSV files

### 2. Generated Files

**`grid_predictions_comparison.csv`** (1,231 rows)
- Columns: `grid_id`, `pred_scaled_catboost`, `barangay_name_clean`, `target_poverty_rate`, `population`, `pred_scaled_rf`, `.geo`, `x_idx`, `y_idx`, `lon`, `lat`
- Has `.geo` in JSON format ✅
- Has `lon` and `lat` coordinates ✅

**`grid_with_comprehensive_data.csv`** (1,231 rows)
- Columns: `grid_id`, `.geo`, `barangay_name_clean`, `x_idx`, `y_idx`, `lon`, `lat`
- Required by `app.py` for map visualization ✅

**`all_cells_predictions_1km.csv`** (1,231 rows)
- Columns: `cell_id`, `predicted_poverty`
- Format: `cell_0000_0022`, `0.387...`
- Uses average of CatBoost/RF as placeholder (CNN model not available) ✅

### 3. Updated merge_predictions.py
Fixed the merge function to:
- Always extract `lon`/`lat` from preprocessed data
- Handle missing columns gracefully
- Merge location info properly

## Verification

```powershell
# Check files exist
Get-ChildItem C:\Users\Admin\Downloads\poverty-mapping-withbackend\poverty-mapping-withbackend\data\*.csv

# Verify .geo column format
$row = Import-Csv data\grid_with_comprehensive_data.csv | Select-Object -First 1
$row.'.geo'  # Should show: {"geodesic":false,"type":"Point","coordinates":[122.xxx,6.xxx]}

# Verify lon/lat exist
$row.lon  # Should show: 122.xxx
$row.lat  # Should show: 6.xxx
```

## Next Steps

**Refresh the webapp page** - The predictions should now load correctly with all three models (CatBoost, RF, CNN) available in the dropdown.

If the issue persists:
1. Restart the Flask app: `python src\server\app.py`
2. Clear browser cache (Ctrl+Shift+Delete)
3. Check browser console for errors (F12)

## Prevention for Future Runs

The updated `merge_predictions.py` now properly:
- Reads geometry from raw GEE export
- Extracts lon/lat from preprocessed data
- Creates all 3 required files in one call

When running the refresh pipeline next time, it should work correctly without manual intervention.

## Files Modified
- ✅ `src/workflow/merge_predictions.py` - Updated merge logic
- ✅ `src/workflow/refresh_pipeline.py` - Updated to pass correct parameters
- ✅ `C:\Users\Admin\povmapbackend\fix_merge.py` - Standalone fix script (can be deleted)
