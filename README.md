# Mars Imagery Explorer (Prototype)

## Project Overview

Mars Imagery Explorer is an experimental submission for the 2025 **NASA Space Apps Challenge** (problem statement: *Embiggen Your Eyes!*). The concept targets the difficulty of navigating NASA’s gigapixel-scale imagery by stitching, tiling, and streaming HiRISE scenes for responsive exploration in a web browser. Users should be able to browse available scenes, inspect preview imagery, zoom or pan across tiles, and request latitude/longitude crops for offline analysis. The current codebase is **not working** end‑to‑end; backend APIs and frontend widgets exist, but the integrated map/preview experience remains unstable and incomplete.

## Challenge Alignment

- **Zooming massive datasets:** Converts HiRISE JP2 scenes into manageable JPEG tiles and exposes them through a REST API.
- **Feature discovery:** Designed to highlight regions via map overlays and provide custom cropping for potential labelling workflows.
- **Public & research audiences:** Envisions a single-page app that works in classrooms, museums, or research labs without specialist tooling.
- **Extensibility:** Architecture could incorporate additional datasets (Earth observations, lunar maps, time-series imagery) by swapping manifests and tile sources.

## Architecture & Flow

```
┌────────────────────┐       ┌────────────────────────────┐
│   Data Pipeline    │       │        REST Backend        │
│  (converter.py)    │       │        (FastAPI)           │
│• Iterate JP2 files │   →   │• /api/images               │
│• Generate JPEG     │       │• /api/images/{scene}/tiles │
│  tiles + metadata  │       │• /api/images/{scene}/crop  │
└────────────────────┘       │• /api/images/{scene}/full  │
                             └──────────────┬─────────────┘
                                            │
                                            ▼
                                 ┌────────────────────────┐
                                 │  Frontend (Leaflet +   │
                                 │   vanilla JS modules)  │
                                 │• Scene selection list  │
                                 │• Preview + crop UI     │
                                 │• Map overlay rendering │
                                 └────────────────────────┘
```

### Intended User Journey
1. **Initialize dataset:** Run `manage.py start backend frontend` after the converter has produced manifests/tiles from local HiRISE JP2 archives.
2. **Browse scenes:** Frontend fetches `/api/images` to build a catalogue showing target code, orbit, resolution, and available previews.
3. **Inspect imagery:** Selecting a scene should load a cached preview image and, when geospatial metadata is present, activate the Leaflet map with stitched tiles.
4. **Request crops:** Users specify latitude/longitude bounds; backend streams a stitched crop for download and highlights the requested footprint on the map.

## Components & Technologies

| Layer | Key Elements |
|-------|--------------|
| **Backend** | Python 3.11, FastAPI, Uvicorn (dev server), Pydantic, custom dataset service, Pillow (planned for image processing) |
| **Frontend** | HTML/CSS, vanilla JavaScript modules, Fetch API, Leaflet.js for mapping |
| **Tooling** | Git, Python virtual environment, make-like `manage.py` helper, macOS shell scripts |
| **Data Handling** | Local filesystem for manifests, tiles, temporary crops: `metadata.json` + JPEG outputs |

## Datasets & Resources

- **Primary imagery:** HiRISE JP2 scenes from NASA’s Planetary Data System (e.g., `ESP_089206_2620_RED.JP2`, `ESP_089293_2620_COLOR.JP2`).
- **Reference material:** NASA/USGS HiRISE documentation, challenge background notes, Leaflet & FastAPI guides, FITS/JP2 conversion utilities.
- **Planned enhancements:** Add support for other missions (Earth observation mosaics, Solar System Treks DEMs) once core pipeline stabilises.

## Current Status & Known Issues

- Preview and tile endpoints exist, but the automatic fallback hierarchy needs reliability improvements.
- Map stays disabled for scenes lacking geospatial metadata; additional UX cues required.
- Image converter assumes local JP2 availability; no download automation or checksum validation is bundled.
- No authentication, persistence, or annotation features yet—critical for feature labelling workflows requested in the challenge brief.
- Deployment scripts are missing; only local development on macOS has been exercised.

## Development Environment

- **Hardware:** Apple silicon laptop with 16 GB RAM (development). Testing on comparable macOS hardware only.
- **Operating System:** macOS Ventura.
- **Software Dependencies:** Python 3.11, pip/venv, optional Pillow/GDAL for imaging, system `http.server` for static hosting, Git for source control.
- **Workflow:** Manual tiling via `backend/app/utils/converter.py`, local REST testing using Uvicorn reload mode, browser-based UI checks (no automated test suite yet).

## Setup & Usage (Experimental)

1. Create and activate a Python virtual environment; install requirements once a `requirements.txt` is finalised (not included yet).
2. Place HiRISE JP2 files under `data/` following the expected `target/orbit/scene.jp2` structure.
3. Run the converter: `python backend/app/utils/converter.py data --recursive --force --format jpg`.
4. Start services: `python manage.py start` (spawns FastAPI backend at `:8000` and static frontend at `:4173`).
5. Open the frontend in a browser (`http://localhost:4173`) and test scene selection & crop workflows.

> **Note:** Because the project is not fully functional, expect broken previews, failed crop requests, and map placeholder states. Logs are available under `logs/` for troubleshooting.

## Roadmap

- Stabilise preview pipeline; generate low-resolution thumbnails during conversion to avoid large `/full` downloads.
- Add automated tests for dataset loading, tile math, and frontend interactions.
- Integrate feature labelling/annotation tools with exportable GeoJSON.
- Support temporal comparisons (e.g., differencing two orbits) and multi-instrument overlays.
- Package deployment using Docker or a lightweight PaaS script for reproducibility.

## Credits & Acknowledgements

- NASA/JPL/University of Arizona for HiRISE imagery.
- NASA Space Apps organisers for the challenge brief and resource links.
- Open-source maintainers of FastAPI, Leaflet, and associated Python/JS ecosystems.

