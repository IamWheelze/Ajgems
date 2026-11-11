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
1. Copernicus Data Space (FRD primary source) added 2026-11-11 as provider `cdse`. Verified with `minefoot doctor`: search, asset names, and offset −1000 on baseline 05.12 from asset fields `raster:offset`/`raster:scale` (scene of 2026-02-27, the same as Planetary Computer's). **Not verified: pixel reads.** Pixels are JPEG 2000 on CDSE's S3 store and need free S3 keys (`CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY`), which this environment does not have. When both are set, CDSE becomes the first provider; without them, fetches refuse with a clear message.
2. Legal review of share-alike (CC BY-SA 4.0) labels: Global ML-ready and Maus v2. Licences were read from source records on 2026-11-03, see `docs/licence_register.csv`. Planetary Computer's terms limit it to evaluation and testing, not business-critical use.

## Next steps (FRD section 15)
1. Done: `pytest`, `minefoot demo`, `minefoot doctor`.
2. One real Nigerian scene fetched and checked (see above). Inspect the gallery with the founder.
3. Done 2026-11-03: licence register filled from source pages. Only Google Earth imagery and the NICFI agreement text remain unread; Global ML-ready and Maus v2 need a lawyer on share-alike.
4. Done 2026-11-03: Global ML-ready adapter (`minefoot ingest`, `fetch-sites`, `label-audit`), 52 sites fetched. Cloud-mask issue: per-polygon masking report built (see above).
5. Done 2026-11-06: `splits.py` with `minefoot overlaps`, `split`, `lock-test` and `check-split`. Units join sites by overlap, shared scene, 50 km block and 10 km buffer; each unit goes wholly to one split, stratified by mine scale. A test lock is a hashed site list: locked sites stay in test, new sites next to them are held out, and edits are detected. Global ML-ready split v1 (845/181/181) is in `docs/evidence/`, not locked yet. The authors' own split has 21 cross-split pairs within 10 km, so do not use it.
6. Done 2026-11-06: `minefoot view` writes the full FR-133 package: gallery, COGs (display layers, reflectance, SCL), polygons in the scene CRS styled by `mask_status`, `.qml` styles and a relative-path `project.qgs` (`qgis_project.py`). Verified in QGIS 3.34 (Ubuntu `python3-qgis`, run with `/usr/bin/python3.12`): all layers valid, project CRS set, identified pixel values match rasterio. `scripts/check_qgis_project.py` repeats this check, and the test suite runs it when QGIS bindings are found. QGIS 3.34 ignores `projectCrs` unless `ProjectionsEnabled` is set.
7. Have the founder digitise and lock 30 to 50 Nigerian sites and hard negatives.
8. G1 done 2026-11-06 as internal research (founder approved; Global ML-ready only, 60/60/60 sites): `baseline.py`, `minefoot g1`. Test pixel F1: random forest 0.23 (0.18–0.29), negative-NDVI threshold 0.18 (0.14–0.24); random forest false candidates 272 per 1,000 km², mostly bare fields and dry stream beds. Results: `docs/g1_results.md`. Split v1's test set is now locked (`docs/evidence/test_lock_global_ml_ready_v1.json`); respect it with `split --lock`. 2026-11-07: paired comparison (`minefoot compare`) and typed hard negatives (`negatives.py`, WorldCover proxy, digitising template and checker) done. WorldCover-typed negatives made the random forest worse on validation in both runs (`g1_hn`, `g1_hn2`), because mine ground shares WorldCover's bare, water and built-up classes; `g1_v1`'s random forest stays the baseline. Keep WorldCover for per-type false-alert breakdowns only. See `docs/g1_results.md`. Next: the G2 U-Net with tracked runs.
9. G2 built 2026-11-07 (`g2.py`, `minefoot g2`): U-Net with ResNet-34 encoder, normalisation fitted on train only, checkpoint by validation-chip loss, whole-tile scoring with overlap, then the same evaluation as G1 (`baseline.score_held_out`, `choose_thresholds`, `evaluate_scores`). Tested on synthetic sites and in short CPU runs on real sites. **Founder decision: train on the founder's own GPU machine.** No folder from that machine is shared with the cloud session, so follow `docs/g2_local_run.md`. It rebuilds the data (`download-global-ml-ready`, `ingest`, `split --expect`, `get-sites`), trains, and compares with the committed G1 metrics (`docs/evidence/g1_v1/metrics.json`); `compare` refuses runs scored on different data. The licence of the pretrained encoder weights is not verified yet.
10. **G2 passed, 2026-11-08** (founder chose a CPU run in the cloud session instead; run `g2_v1`, resumed twice after container restarts). Paired against the G1 random forest on validation: pixel F1 +0.208 (+0.147 to +0.282); test +0.197. Test F1 0.43 (0.34–0.56). The gain is recall (+0.29) and precision (+0.15); false-alarm area is unchanged (about 33 km² per 1,000 km² on validation), concentrated in a few dry landscapes and one lake attached to mines. The false-candidate count misses these, so `false_alarm_km2_per_1000km2` is now reported too. See `docs/g2_results.md` and `results/g2_v1_unet_errors.html`. Next: G3 (Nigerian test, no Nigerian training) needs step 7 first. The web app may now start.
11. 2026-11-08: false-candidate definition v2 (a patch of 1 ha or more that is under half labelled; `minefoot rescore` applied to `g1_v1` and `g2_v1`), plus false-candidate area. Hard negatives mined from `g2_v1`'s errors on training sites (`minefoot mine-negatives`), then a fine-tune (`g2_hn`): on validation, false-alarm area −31% (33.3 → 22.9 km² per 1,000 km², paired, interval below zero) and precision +0.076, with recall not clearly changed. **`g2_hn` is the current best model** (test F1 0.48). Digitised negatives plug in with `minefoot rasterize-negatives`, then `g2 --negatives-root`. See `docs/g2_results.md`. G2 needs a GPU decision: ask the founder before using paid or university compute.

12. 2026-11-08: Nigerian labelling kit built (`minefoot labelling-kit configs/nigeria_aois.yaml`; Jos Plateau 2025-12-29 and Kogi/Kwara 2026-01-06, scenes pinned). Founder digitises and locks sites with `docs/nigeria_labelling_kit.md`. G3 waits for the locked labels. Do not run the model on these regions before the labels are locked (test independence, ML-07). `minefoot g3` is built and tested on synthetic data (`g3.py`): it verifies the lock, uses the global-validation threshold, scores each site in a 1 km window (protocol: label all mining within 1 km of a site), and reports site recall against FRD O1, the gap to global, per-type hard-negative rates and unverified candidates outside the windows. Each run is logged in `docs/evidence/nigeria_test_access_log.csv`. F1 is now 2TP/(2TP+FP+FN), so it is 0 rather than undefined when nothing is predicted; all earlier reported values are unchanged.
13. Web app started 2026-11-08, slice W1 (`web/`, see `web/README.md`). Django + GeoDjango + DRF + PostGIS backend with roles, organisation isolation, append-only reviews and audit enforced by database triggers, and area upload and validation; 17 tests. React + MapLibre frontend: sign-in, projects, areas, and a review workspace with dated scene imagery. `minefoot.vectorise` turns score maps into candidate polygons with the masking report. `load_demo` loads `g2_hn` test-site candidates as internal research. W2 done 2026-11-09: detection jobs through Celery (`projects/pipeline.py`, `tasks.py`, `jobs.py`).
- *Flow:* estimate (scene search, 10 km tile grid, model card with Nigerian-domain warning), start, then per tile: clearest covering scene, windowed fetch, quality check (FR-052 four outcomes), score with `MINEFOOT_MODEL_RUN`, vectorise with the masking report. Candidates are merged across tile edges.
- *Controls:* cancel, and resume without repeating tiles.
- *Embargo:* jobs over the Nigerian labelling regions are refused until the test lock exists.
- *Screens:* run setup and a job monitor with a hatched "insufficient" outcome.
- *Verified:* 21 backend tests, and a real 4-tile Ghana job in under a minute on CPU.
W3 done 2026-11-09:
- *Export packages* (FR-106 to FR-109): data, PDF report with coverage map and limits, licence notice, SHA-256 manifest.
- *Review tools:* "not assessed" hatch in review (FR-125), imagery mosaics per candidate, boundary editing with server-simplified valid outlines, stair-step smoothing in `minefoot.vectorise`.
- *Multi-factor sign-in* (FR-001): TOTP; secrets Fernet-encrypted (`MINEFOOT_MFA_KEY`); privileged users refused until enrolled; admin site needs a verified session.
- *Verified:* 30 backend tests and browser checks.
Model registry done (`web/backend/registry`, FR-101 to FR-104): lineage required, paired comparison on locked sites before promotion, second-person approval, one-step rollback; jobs use the production model. `g2_v1` and `g2_hn` are registered; nothing is approved yet (a two-person decision). Dataset explorer done (`web/backend/datasets`, FR-131; Global ML-ready v1 imported: 1,207 sites, no leakage). Swipe comparison between band presets done (FR-127 MVP). Run dashboard done (FR-130; `minefoot curves`): `g2_hn`'s scores are overconfident (top score bin is 0.73 labelled on validation, 0.48 on test; see `docs/g2_results.md`).
2026-11-11, remaining MVP items built (details in `web/README.md`):
- *Review:* missed sites as append-only false negatives (FR-093), server-side filters (FR-094), NDVI/NDWI presets with documented stretches plus an optional labelled per-view stretch (FR-121, FR-122), quality-mask key (FR-123), model-score heatmap (FR-124), pixel inspector checked at 20 points (FR-128), cursor coordinates (FR-120).
- *Jobs:* threshold and minimum-size preview from stored score maps, matching the job at its own settings; saving makes a derived job (FR-126). Tile checksums, scene retrieval time, configurable coverage thresholds (FR-021, FR-023, FR-053). Time so far and usable share of the area (FR-051, FR-132).
- *ML:* error maps on locked test sites, worst F1 first (FR-129).
- *Verified:* 41 backend tests, 65 package tests and browser checks.
- *Not built:* side-by-side and blink comparison (FR-127, Pilot), TiTiler, export signing (FR-109, Commercial), season-of-year domain flag (FR-083; the Nigerian-domain warning exists).

Do not start the web app until gate G2 has passed. Ask the founder before anything that costs money, and before using university compute or data for company work.
