# Poverty Mapping Project

A geospatial analysis and visualization tool for predicting and mapping poverty levels using satellite imagery and machine learning.

## Features

- **Data Extraction**: Automated pipeline for collecting satellite imagery from Google Earth Engine
- **Machine Learning**: Multiple model support (CatBoost, Random Forest, CNN) for poverty prediction
- **Interactive Web Interface**: Map-based visualization with layer toggles and model comparison
- **Automated Updates**: Scheduled data refresh pipeline for quarterly updates

## Quick Start

### Prerequisites

- Python 3.8+
- Google Earth Engine account (for data extraction)
- Node.js 14+ (for development)

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd povMapSite
   ```

2. Set up Python environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements-gee.txt
   ```

3. Authenticate with Google Earth Engine:
   ```bash
   earthengine authenticate
   ```

### Running the Application

1. Start the development server:
   ```bash
   python -m src.server.app
   ```

2. Open your browser to:
   ```
   http://localhost:8000
   ```

## Project Structure

```
povMapSite/
├── data/                   # Data files (geojson, csv, etc.)
├── notebooks/              # Jupyter notebooks for analysis
├── src/
│   ├── gee/               # Google Earth Engine data extraction
│   ├── model/             # ML model implementations
│   ├── server/            # Flask web server
│   └── workflow/          # Data processing pipelines
├── static/                # Frontend assets (JS, CSS, images)
├── tests/                 # Test suite
└── web/                   # Web interface files
```

## Development

### Running Tests

```bash
pytest tests/
```

### Updating Dependencies

1. Install new packages:
   ```bash
   pip install <package>
   ```

2. Update requirements file:
   ```bash
   pip freeze > requirements-gee.txt
   ```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Google Earth Engine for satellite imagery
- OpenStreetMap for base map data
- Various open-source Python libraries
