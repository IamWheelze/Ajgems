# minefoot: Milestone 1 pipeline

Finds and downloads Sentinel-2 L2A images, converts them to reflectance, checks usable coverage and builds a visual gallery. No model exists yet.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
minefoot demo --out data/demo            # synthetic scenes; open data/demo/view/gallery.html
minefoot doctor                          # real catalogue check; must be run first
minefoot search --bbox 8.80,9.70,8.95,9.85 --start 2026-01-01 --end 2026-02-28
minefoot fetch  --bbox 8.80,9.70,8.95,9.85 --start 2026-01-01 --end 2026-02-28 --out data/raw
minefoot prepare data/raw/<SCENE_ID> --out data/prepared
minefoot view data/prepared/<SCENE_ID> --out data/view
minefoot inspect-dataset path/to/dataset.gpkg
```

Open `gallery.html` to inspect a scene. The files in `gis/` open in QGIS.

See `CLAUDE.md` for status and next steps, and `docs/FRD.md` for the full requirements.
