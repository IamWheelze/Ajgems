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
- SCL classes bright tailings and pits as cloud. On 52 sites, 2.35% of labelled pixels are classed cloud versus 0.19% outside; 6 sites lose more than 5%. `quality.NOT_USABLE` still drops them.
- **Founder decision, 2026-11-06:** keep the current mask, and report how much of each polygon (candidate, label or area) it removed. Built: `quality.polygon_coverage`, `labels.polygon_mask_report`, `minefoot mask-report`, gallery warning and per-polygon table, masked pixels in magenta, and mask columns in the exported `labels.gpkg`. Thresholds are provisional: partly masked at 5%, mostly masked at 50%. Candidates from a future model must go through `polygon_mask_report` before they are shown or exported. Changing the mask itself (options 1 and 2 in the findings doc) is still open.

Still unverified:
1. Copernicus Data Space is the FRD's stated primary source but is not implemented; only Planetary Computer and Earth Search are. Add it or update the FRD.
2. Legal review of share-alike (CC BY-SA 4.0) labels: Global ML-ready and Maus v2. Licences were read from source records on 2026-11-03, see `docs/licence_register.csv`. Planetary Computer's terms limit it to evaluation and testing, not business-critical use.

## Next steps (FRD section 15)
1. Done: `pytest`, `minefoot demo`, `minefoot doctor`.
2. One real Nigerian scene fetched and checked (see above). Inspect the gallery with the founder.
3. Done 2026-11-03: licence register filled from source pages. Only Google Earth imagery and the NICFI agreement text remain unread; Global ML-ready and Maus v2 need a lawyer on share-alike.
4. Done 2026-11-03: Global ML-ready adapter (`minefoot ingest`, `fetch-sites`, `label-audit`), 52 sites fetched. Cloud-mask issue: per-polygon masking report built (see above).
5. Done 2026-11-06: `splits.py` with `minefoot overlaps`, `split`, `lock-test` and `check-split`. Units join sites by overlap, shared scene, 50 km block and 10 km buffer; each unit goes wholly to one split, stratified by mine scale. A test lock is a hashed site list: locked sites stay in test, new sites next to them are held out, and edits are detected. Global ML-ready split v1 (845/181/181) is in `docs/evidence/`, not locked yet. The authors' own split has 21 cross-split pairs within 10 km, so do not use it.
6. Done 2026-11-06: `minefoot view` writes the full FR-133 package: gallery, COGs (display layers, reflectance, SCL), polygons in the scene CRS styled by `mask_status`, `.qml` styles and a relative-path `project.qgs` (`qgis_project.py`). Verified in QGIS 3.34 (Ubuntu `python3-qgis`, run with `/usr/bin/python3.12`): all layers valid, project CRS set, identified pixel values match rasterio. `scripts/check_qgis_project.py` repeats this check, and the test suite runs it when QGIS bindings are found. QGIS 3.34 ignores `projectCrs` unless `ProjectionsEnabled` is set.
7. Have the founder digitise and lock 30 to 50 Nigerian sites and hard negatives.
8. G1 done 2026-11-06 as internal research (founder approved; Global ML-ready only, 60/60/60 sites): `baseline.py`, `minefoot g1`. Test pixel F1: random forest 0.23 (0.18–0.29), negative-NDVI threshold 0.18 (0.14–0.24); random forest false candidates 272 per 1,000 km², mostly bare fields and dry stream beds. Results: `docs/g1_results.md`. Split v1's test set is now locked (`docs/evidence/test_lock_global_ml_ready_v1.json`); respect it with `split --lock`. Next: hard negatives (FR-074), a paired site bootstrap for model comparison, then the G2 U-Net with tracked runs. G2 needs a GPU decision: ask the founder before using paid or university compute.

Do not start the web app until gate G2 has passed. Ask the founder before anything that costs money, and before using university compute or data for company work.
