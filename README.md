# minefoot: Milestone 1 pipeline

Finds and downloads Sentinel-2 L2A images, converts them to reflectance, checks usable coverage and builds a visual gallery. No model exists yet.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
minefoot demo --out data/demo            # synthetic scenes; open data/demo/view/gallery.html
minefoot doctor                          # real catalogue check; must be run first
minefoot search --bbox 8.80,9.70,8.95,9.85 --start 2026-01-01 --end 2026-02-28
minefoot fetch  --bbox 8.80,9.70,8.95,9.85 --start 2026-01-01 --end 2026-02-28 --out data/raw   # add --provider earth-search to pick a catalogue
minefoot prepare data/raw/<SCENE_ID> --out data/prepared
minefoot view data/prepared/<SCENE_ID> --out data/view
minefoot inspect-dataset path/to/dataset.gpkg

# Global ML-ready: ingest, fetch exact scenes, check labels and masking
minefoot ingest global-ml-ready path/to/mining_area_data.gpkg --out data/datasets/global_ml_ready
minefoot fetch-sites data/datasets/global_ml_ready/sites.gpkg --n 50 --out data/raw/global_ml_ready
minefoot label-audit data/prepared/global_ml_ready/*/ --raw-root data/raw/global_ml_ready
minefoot mask-report data/prepared/<SCENE> --polygons polygons.geojson --out data/mask/<SCENE>.gpkg

# Overlaps and splits by site and geographic block
minefoot overlaps data/datasets/global_ml_ready/sites.gpkg --out data/splits/overlaps.csv
minefoot split data/datasets/global_ml_ready/sites.gpkg --out data/splits/split.gpkg
minefoot check-split data/splits/split.gpkg
minefoot lock-test data/splits/split.gpkg --out data/splits/test_lock.json
```

`minefoot view` writes one inspection package (FR-133): `gallery.html`, cloud-optimised GeoTIFFs and a GeoPackage in `gis/`, styles in `styles/`, and `project.qgs`. Open `project.qgs` in QGIS 3.34 or later; paths are relative, so the folder can be zipped and moved. Use `--labels-root data/raw/global_ml_ready` to overlay each site's labels. The OpenStreetMap base layer needs internet access and is off by default.

To check a package in real QGIS (needs a Python with QGIS bindings; the test suite runs this automatically when one is found):

```bash
QT_QPA_PLATFORM=offscreen python3 scripts/check_qgis_project.py data/view/project.qgs
```

See `CLAUDE.md` for status and next steps, and `docs/FRD.md` for the full requirements.
