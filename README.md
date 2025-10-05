# Mars Imagery Explorer (Prototype)

## Overview

Mars Imagery Explorer is an experimental submission for the 2025 NASA Space Apps Challenge ("Embiggen Your Eyes!"). The stack prepares HiRISE imagery for responsive exploration by tiling raw JP2 scenes, exposing a FastAPI backend for discovery/cropping, and serving a lightweight Leaflet-based frontend. The project is still a prototype: core services start, REST endpoints respond, and the UI surfaces catalogue, preview, and crop workflows, but the integrated experience remains unstable and incomplete.

## Current Capabilities

- Backend FastAPI application with dataset initialisation, preview, tiling, search, crop, and full-scene download endpoints.
- Dataset service that invokes `backend/app/utils/converter.py` to tile JP2 scenes and builds `metadata.json` manifests for each scene.
- Leaflet frontend (`frontend/`) that lists scenes, previews imagery, toggles map overlays when geospatial metadata is present, and drives crop/full download requests.
- `manage.py` helper to start or stop the backend (Uvicorn) and frontend (Python `http.server`) with PID tracking and rolling logs.
- Utility scripts under `backend/app/utils/` for searching NASA's HiRISE index, downloading source imagery, converting JP2 products, and re-stitching tiles.

## Repository Layout

```
project_root/
|-- backend/
|   |-- app/
|   |   |-- api/            # FastAPI routes, request models, background helpers
|   |   |-- services/       # Dataset/search orchestration logic
|   |   `-- utils/          # Converter, restitcher, HiRISE search/download tools
|   `-- requirements.txt    # Backend/runtime dependencies (FastAPI, Pillow, pvl, etc.)
|-- frontend/
|   |-- index.html          # Leaflet UI shell
|   |-- app.js              # Scene list, preview, map, and crop handlers
|   `-- styles.css          # Basic styling for the prototype UI
|-- data/                   # Not versioned; expected location of raw scenes and generated tiles
|   |-- <target_code>/
|   |   `-- <orbit>/        # Place downloaded JP2 + matching LBL files here
|   `-- tiles/
|       `-- <scene_id>/     # Populated by the converter with JPEG tiles + metadata.json
|-- docs/                   # HiRISE reference PDFs provided by NASA/USGS
|-- logs/                   # Service logs written by manage.py (backend.log, frontend.log)
|-- manage.py               # CLI to manage backend/frontend processes
`-- README.md
```

> The `data/`, `.run/`, `.venv/`, `logs/`, and other generated directories are ignored in version control. Only code, configuration, and documentation live in the repository.

## HiRISE Data Prerequisites (Not Included)

The repository does **not** ship imagery because HiRISE scenes are large and subject to NASA PDS distribution rules. You must download JP2 products (and their `.LBL` metadata) yourself before the backend can build manifests. The stack expects the following convention under the project root:

```
data/
|-- <target_code>/                # e.g. 2620 for Olympus Mons region
|   `-- <orbit_number>/           # zero-padded orbit folder, e.g. 089212
|       |-- ESP_089212_2620_RED.JP2
|       |-- ESP_089212_2620_RED.LBL
|       `-- ... (other bands or colour products)
|-- tiles/                        # created by the converter
```

### How to Source Imagery

1. **Search NASA's HiRISE index (recommended):**
   - Ensure the backend requirements are installed (see Setup below).
   - Run `python backend/app/utils/finding_image.py 2620 --limit 10` to locate scenes for target code `2620` (replace with your target).
   - Download directly via the helper: `python backend/app/utils/finding_image.py 2620 --download --limit 4 --outdir data/raw/2620`.
   - Move each JP2/LBL pair into `data/<target_code>/<orbit>/`, where `<orbit>` is the six-digit orbit extracted from the filename (e.g. `089212`).

2. **Manual download from NASA PDS:**
   - Browse https://hirise-pds.lpl.arizona.edu/PDS/INDEX/ for the `RDRINDEX.TAB` catalogue.
   - Use the URLs listed there (or the public product pages on https://hirise-pds.lpl.arizona.edu/) to fetch both the `.JP2` image and matching `.LBL` for each scene you need.
   - Preserve the original file names and place them in the structure shown above.

3. **Keep the repository clean:**
   - Do not commit raw imagery or generated tiles to source control; they are intentionally excluded via `.gitignore`.
   - Large downloads can live outside the repo and be symlinked into `data/` if disk space is a concern.

> Tip: Install the optional `pvl` dependency so the converter can parse `.LBL` files and expose latitude/longitude bounds for the frontend map and crop API.

## Converting Scenes into Tiles

Once JP2 files and labels are staged:

1. Activate your virtual environment and install dependencies: `python -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt` (repeat `source .venv/bin/activate` on subsequent shells).
2. Run the converter directly: `python backend/app/utils/converter.py data/2620 --recursive --format jpg --tile-size 2048 --quality 85`.
3. Alternatively, call the backend endpoint (frontend `Initialise dataset` button or `POST /api/init`) to trigger the same converter with configurable options.
4. After a successful run you should see per-scene folders under `data/tiles/<scene_id>/` containing tile JPEGs and a `metadata.json` manifest with image size, tile grid, and geospatial metadata (if available).

`backend/app/utils/restitcher.py` can reassemble full scenes or crops locally from those manifests, and is the utility used by the `/api/images/{scene}/crop` and `/api/images/{scene}/full` endpoints.

## Running the Stack

1. **Backend/Frontend services:**
   - Start via the helper: `python manage.py start` (spawns FastAPI on `http://localhost:8000` and a static frontend on `http://localhost:4173`).
   - `python manage.py stop`, `status`, and `restart` are available as well.
   - Logs append to `logs/backend.log` and `logs/frontend.log`; inspect them when debugging startup or converter issues.

2. **Direct backend development:**
   - Run `uvicorn backend.app.main:app --reload --port 8000` if you prefer a manual server start during development.
   - The frontend assets can be served via `python -m http.server 4173 --directory frontend`.

3. **API smoke tests:**
   - `GET /api/health` checks service health.
   - `GET /api/images` lists manifests discovered under `data/`.
   - `POST /api/init` triggers a conversion (set `force=true` to rebuild tiles even when manifests exist).
   - `GET /api/images/{scene_id}/preview`, `/crop`, `/tiles/{row}/{col}`, and `/full` download preview imagery, render map tiles, crop by lat/lon, or assemble the entire scene respectively.
   - `GET /api/search?q=<scene_or_target>` proxies the HiRISE index lookup exposed by `finding_image.py`.

## Frontend Workflow

- Visit `http://localhost:4173` once services are running.
- Use the **Initialise dataset** button to launch conversion jobs if tiles are missing.
- Select a scene from the catalogue to view metadata, fetch preview imagery, and (if projections are available) enable the Leaflet map overlay.
- Provide latitude/longitude bounds to request a crop; the frontend calls `/api/images/{scene}/crop` and downloads the stitched PNG.
- Download the full-resolution JPG via the dedicated button (internally calls `/api/images/{scene}/full`).

## Known Limitations

- Previews and map overlays rely on optional label metadata; scenes without `.LBL` files will not expose geospatial bounds or crops.
- Converter success depends on having FFmpeg, GDAL, ImageMagick, or GraphicsMagick available on PATH. Supply `--converter` to force a specific tool if detection fails.
- No automated tests exist yet; regressions across dataset parsing, tiling maths, and frontend interactions remain a risk.
- Deployment is local-only, with assumptions tuned for macOS; Linux/Windows paths and service scripts have not been hardened.

## Roadmap Ideas

- Harden the tiling pipeline (progress feedback, resumable jobs, checksum validation).
- Provide packaged downloads or Docker files for consistent local setups.
- Add unit tests around dataset manifests, projection math, and frontend state management.
- Integrate richer annotation/feature-labelling tools aligned with the Space Apps brief.
- Expand dataset support beyond HiRISE by swapping manifests and tile sources.

## Credits

- NASA/JPL/University of Arizona for HiRISE imagery and documentation.
- NASA Space Apps Challenge organisers for the prompt and briefing materials.
- The FastAPI, Leaflet, Pillow, Requests, and broader open-source communities powering this prototype.
