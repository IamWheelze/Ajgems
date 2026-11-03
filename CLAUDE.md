# Mining footprint detection: project handoff

Read `docs/FRD.md` before planning. `docs/original-brief.txt` is the founder's original request. The founder is an MSc remote-sensing student and a Django, React and Python developer.

## Non-negotiable product rules
- Detect visible mining-related disturbance only. Never imply mineral type, deposits, legality, ownership or current activity (FR-110).
- Keep "no candidate found" separate from "insufficient usable imagery" (FR-052).
- Say "model score" until calibration is tested. Never invent or report results that were not measured.
- Do not train through the datasets one after another. Follow the gates in FRD section 8.3.
- Reviewed predictions reach ground truth only through independent QC (FR-075).
- Split by site and geographic block. The Nigerian test set stays locked and independent.
- Use one preprocessing path for training and inference (`minefoot.preprocess`).
- Do not use a dataset for training until `docs/licence_register.csv` records its licence. A code licence does not cover its data.

## Current state: Milestone 1, first part
Built:
- STAC search and windowed download with provider fallback (`catalog.py`)
- Baseline-aware reflectance (`reflectance.py`), input schema (`schema.py`)
- SCL masks, coverage, tile outcomes (`quality.py`), chip grid (`preprocess.py`)
- Gallery, histograms and QGIS-ready GeoTIFFs (`viewer.py`)
- Label rasterising and dataset inspection (`labels.py`), CLI (`cli.py`), synthetic scenes (`synthetic.py`)

Verified: 25 tests pass and the gallery was checked visually, all on **synthetic scenes and local fake files**.

**Never run against real catalogues.** The build environment blocked them. Unverified assumptions, to check first:
1. Provider collection and asset names in `PROVIDERS`.
2. Offset metadata: `earthsearch:boa_offset_applied`, `raster:bands`, `s2:processing_baseline`. Confirm against a real scene and the gallery histograms.
3. Scene and pixel access through Planetary Computer signing and Earth Search.
4. Global ML-ready columns: `tile_id`, `s2_tile_id`, `split` were read from the authors' code, not the released file. Run `minefoot inspect-dataset`.
5. Copernicus Data Space is the FRD's stated primary source but is not implemented; only Planetary Computer and Earth Search are. Add it or update the FRD.
6. Whether commercial use of each catalogue's terms is acceptable.

## Next steps (FRD section 15)
1. Run `pytest`, `minefoot demo`, then `minefoot doctor`. Fix any real-catalogue mismatch.
2. Fetch one real Nigerian scene and inspect the gallery with the founder.
3. Fill the `NOT VERIFIED` rows in `docs/licence_register.csv` from the source pages.
4. Add the Global ML-ready adapter, and fetch about 50 sites using the dataset's exact scene IDs (`fetch --scene-id`).
5. Add overlap detection and site or block splits (FR-072, FR-077).
6. Add a saved QGIS project. FR-133 needs one and it is not built yet.
7. Have the founder digitise and lock 30 to 50 Nigerian sites and hard negatives.
8. Build the G1 baseline: an index threshold and a random forest. Then build the G2 U-Net with tracked runs.

Do not start the web app until gate G2 has passed. Ask the founder before anything that costs money, and before using university compute or data for company work.
