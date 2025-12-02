# Workspace Structure - Self-Contained Poverty Mapping Application

This workspace is now **fully self-contained** and can be cloned and run without any external dependencies (except Python packages).

## Directory Structure

```
poverty-mapping-withbackend/
├── src/                       # Main source code
│   ├── server/app.py          # Flask backend
│   ├── model/inference.py     # Model inference
│   ├── workflow/              # Data processing pipelines
│   │   ├── refresh_pipeline.py  # Full data refresh pipeline
│   │   └── merge_predictions.py # Merge model outputs
│   └── config.py              # Configuration
├── scripts/                   # Processing scripts (formerly in povmapbackend)
│   ├── geospatial_prep.py     # GEE data extraction
│   ├── preprocess_grid_data.py # Grid preprocessing
│   ├── train_catboost_model.py # CatBoost training
│   ├── train_rf_model.py       # Random Forest training
│   └── cnn_data_preprocessing.py # CNN preprocessing
├── assets/                    # Static assets (formerly in povmapbackend/assets)
│   ├── grid_with_comprehensive_data.csv
│   ├── shapefile/             # ROI shapefiles
│   ├── socioeconomic_csv/     # Socioeconomic data
│   └── *.geojson              # POI and road data
├── output/                    # Model outputs (formerly in povmapbackend/output)
│   ├── catBoost/geospatial_disagg/
│   │   ├── grid_predictions.csv
│   │   └── feature_columns.json
│   ├── rf/geospatial_disagg/
│   │   ├── grid_predictions.csv
│   │   └── feature_columns.json
│   ├── grids/
│   └── cnn_reuse_1km_*/       # CNN outputs per year
├── googleEarthExports/        # GEE export data
│   └── zc04_grid_data_2024.csv
├── env/                       # Environment config (GEE credentials)
│   └── ee-zc-povertymapping-*.json
├── models/                    # Trained ML models
│   ├── catboost_disagg_model.cbm
│   ├── rf_disagg_model.pkl
│   └── pytorch_fusion_cnn/
├── data/                      # Web app data outputs
│   ├── grid_predictions_comparison.csv
│   ├── all_cells_predictions_1km.csv
│   └── shapefile/
├── static/                    # Web frontend
├── web/                       # Additional web assets
└── csv_outputs/               # Socioeconomic CSV data for charts
```

## How the Refresh Pipeline Works

When you click the "Refresh Data" button:

1. **GEE Extraction** (`scripts/geospatial_prep.py`): 
   - Extracts satellite data from Google Earth Engine
   - Outputs to `googleEarthExports/`

2. **Preprocessing** (`scripts/preprocess_grid_data.py`):
   - Attaches barangay info to grid cells
   - Outputs to `assets/grid_with_comprehensive_data.csv`

3. **Model Inference** (`src/model/inference.py`):
   - Runs CatBoost, RF, and CNN models
   - Uses trained models from `models/`
   - Outputs to `output/*/geospatial_disagg/`

4. **Merge Outputs** (`src/workflow/merge_predictions.py`):
   - Combines all model predictions
   - Outputs to `data/grid_predictions_comparison.csv`

## Important Notes

- All paths are now **project-relative** (no external dependencies)
- GEE credentials are in `env/` folder
- Models are pre-trained in `models/` folder
- The refresh pipeline creates outputs in `output/` and `data/`

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run the Flask server
python -m src.server.app
# or
python src/server/app.py

# The web app will be available at http://localhost:8000
```

## Manual Operations

```bash
# Run merge manually
python manual_merge.py

# Run refresh pipeline from CLI
python -m src.workflow.refresh_pipeline --skip-gee

# Test imports
python -c "from src.server.app import app; print('OK')"
```
