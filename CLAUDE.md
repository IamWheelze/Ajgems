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

**Real-catalogue check, 2026-11-03** (`minefoot doctor`, Jos bbox 8.80,9.70,8.95,9.85):
- Verified: collection and asset names in `PROVIDERS` for both providers (no missing assets).
- Verified: offset resolves to -1000 DN on baselines 05.11/05.12 (Planetary Computer by `processing_baseline`, Earth Search by `raster:bands`). Real scene `S2B_T32PMR_20260103T094921_L2A` (Earth Search) gave plausible reflectance, no negatives, and a gallery that looks right.
- Verified: STAC search and pixel reads on both providers (Planetary Computer signing works once `sentinel2l2a01.blob.core.windows.net` is allowed by the network policy).
- Cross-check: scene of 2026-01-03 fetched from both providers (`S2B_MSIL2A_20260103T094319_R036_T32PMR_20260103T122607`, `S2B_T32PMR_20260103T094921_L2A`) gives identical reflectance in all 10 bands, identical SCL and the same grid, despite the offset being found by different rules.
- `fetch` downloads from whichever provider answered `search`; it does not fall back if the pixel download fails. `--provider` picks one explicitly.

**Global ML-ready check, 2026-11-03** (details: `docs/m1_global_ml_ready_check.md`):
- Verified: columns `tile_id`, `s2_tile_id` and `split` are in layer `tiles`; label layers have `tile_id` only. 1,207 usable sites, none in Nigeria.
- Exact scene IDs resolve only on Planetary Computer (baseline 02.12, offset 0). 52 sites fetched, prepared and audited; no label shift; artisanal labels are loose.
- **Open decision:** SCL classes bright tailings and pits as cloud. On 52 sites, 2.35% of labelled pixels are classed cloud versus 0.19% outside; 6 sites lose more than 5%. `quality.NOT_USABLE` drops them. Options are in the findings doc. Ask the founder before changing the mask.

Still unverified:
1. Copernicus Data Space is the FRD's stated primary source but is not implemented; only Planetary Computer and Earth Search are. Add it or update the FRD.
2. Legal review of share-alike (CC BY-SA 4.0) labels: Global ML-ready and Maus v2. Licences were read from source records on 2026-11-03, see `docs/licence_register.csv`. Planetary Computer's terms limit it to evaluation and testing, not business-critical use.

## Next steps (FRD section 15)
1. Done: `pytest`, `minefoot demo`, `minefoot doctor`.
2. One real Nigerian scene fetched and checked (see above). Inspect the gallery with the founder.
3. Done 2026-11-03: licence register filled from source pages. Only Google Earth imagery and the NICFI agreement text remain unread; Global ML-ready and Maus v2 need a lawyer on share-alike.
4. Done 2026-11-03: Global ML-ready adapter (`minefoot ingest`, `fetch-sites`, `label-audit`), 52 sites fetched. Next: decide the cloud-mask issue above.
5. Add overlap detection and site or block splits (FR-072, FR-077).
6. Add a saved QGIS project. FR-133 needs one and it is not built yet.
7. Have the founder digitise and lock 30 to 50 Nigerian sites and hard negatives.
8. Build the G1 baseline: an index threshold and a random forest. Then build the G2 U-Net with tracked runs.

Do not start the web app until gate G2 has passed. Ask the founder before anything that costs money, and before using university compute or data for company work.
