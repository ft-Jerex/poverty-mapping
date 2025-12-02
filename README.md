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

### Web App Pages & Authentication

- **Public landing page**: `http://localhost:8000/`
  - Overview of the project and a public-facing poverty map and analytics section.
  - Uses the same Leaflet + Chart.js visualization as the admin view, but without upload controls.
- **Admin map view**: `http://localhost:8000/admin`
  - Full interactive map and analytics dashboard (current build, now treated as admin side).
  - Intended for authorized users who will manage data updates in future iterations.
- **Login page**: `http://localhost:8000/login`
  - Username/password login and registration for local admin accounts.

Admin credentials are stored in a local SQLite database at `data/users.db` using hashed passwords
(no plaintext passwords).

### Environment Variables

Create a `.env` file in the project root (same folder as `requirements-gee.txt`) with at least:

```bash
FLASK_SECRET_KEY="replace-with-a-random-secret-string"
```

The app uses `python-dotenv` to load these values on startup. `FLASK_SECRET_KEY` is required to
secure the Flask session cookies used for admin login.

### Authentication Behaviour

- New admins can register via the form on `/login`.
- Successful registration or login stores the `username` in the Flask session.
- Access to `/admin` is restricted to authenticated sessions; unauthenticated users are
  redirected to `/login`.

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

## Temporary UI changes

- **Public feedback box:** The landing page feedback/recommendation box (`static/landing.html` `#report-section`) has been commented out temporarily to pause public submissions while incoming messages are validated and processed. The markup is preserved in HTML comments so it can be re-enabled later.
- **Admin "People" tab:** The admin UI people tab and panel (`static/index.html` `#tab-people` and `#panel-people`) have been commented out to remove access to stored messages until review is complete. The markup remains in the files and can be restored when ready.

If you want these features re-enabled, remove the surrounding HTML comment markers (`<!--` / `-->`) in the respective files.
