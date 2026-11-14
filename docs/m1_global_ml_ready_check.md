# Global ML-ready: first real-data check (2026-11-03)

What was run, what was measured, and what still needs a decision. Every number here was measured on the files listed; nothing is estimated.

## What was run

1. Downloaded `mining_area_data.zip` from [Zenodo 14195737](https://zenodo.org/records/14195737) v1.0.0. MD5 matched the record (`1259ad8045ae648c547ef3db1fccab3b`).
2. `minefoot inspect-dataset` on `mining_area_data.gpkg`.
3. `minefoot ingest global-ml-ready`: report in [`evidence/ingest_report_global_ml_ready.json`](evidence/ingest_report_global_ml_ready.json).
4. `minefoot fetch-sites --n 50` (seed 0): 25 African sites and 25 from other regions; 26 artisanal and 24 industrial. All 50 were fetched from Planetary Computer by exact scene ID, with no failures. Two sites fetched earlier as a test (gmlr-1076, gmlr-2805) bring the total to 52. List: [`evidence/fetched_sites_global_ml_ready.txt`](evidence/fetched_sites_global_ml_ready.txt).
5. `minefoot prepare` on all 52 sites, then `minefoot label-audit`: [`evidence/label_audit_global_ml_ready_52.csv`](evidence/label_audit_global_ml_ready_52.csv).

## Findings

**Columns (assumption 1 in CLAUDE.md): confirmed.** `tile_id`, `s2_tile_id` and `split` are in layer `tiles`. The seven label layers (`maus_*`, `tang_*`, `preferred_*`, `test_polygons_validated`) have only `tile_id` and geometry. `test_polygons_validated` covers exactly the 116 `test` tiles.

**Rows.** The file has 1,514 tiles. The adapter keeps 1,207: 890 train, 201 val and 116 test, of which 975 are industrial and 232 artisanal. It drops 236 tiles the authors rejected and 71 with no split. Zenodo's description says 1,210 sites; the 3-site difference is unexplained.

**No tiles are in Nigeria.** None falls inside Nigeria's bounding box, so this dataset cannot leak into the locked Nigerian test set. West African tiles are mostly in Ghana, with a few in Guinea, Mali and Mauritania.

**Exact scenes come only from Planetary Computer.** All sampled IDs resolve there, in processing baseline 02.12 (offset 0). Earth Search has the same acquisitions only as Collection-1 reprocessing (baseline 05.00), under different IDs, and was missing 2 of the 4 checked. Under Planetary Computer's terms this is fine for research use (see the licence register).

**Radiometry.** Baseline 02.12 resolves to offset 0, the other branch of the offset rule, now exercised on real data. All 52 scenes pass the coverage check (usable 95.4 to 100%).

**Label alignment: no systematic shift.** Ten sites were checked by eye: 2 at full tile and 8 chosen at random, cropped at a label edge ([`evidence/label_alignment_a.png`](evidence/label_alignment_a.png), [`_b`](evidence/label_alignment_b.png)). Industrial outlines follow pit and dump edges closely. **Artisanal outlines are loose**: they enclose forest between workings (gmlr-1083, gmlr-1061, gmlr-2714). Pixel-level training on these labels will teach some vegetation as "mining", which matters for Nigerian artisanal sites.

## Open decision: bright mine ground is classed as cloud

The L2A scene classification (SCL) marks bright tailings and pit surfaces as **medium-probability cloud** (class 8), sometimes high (9). `quality.NOT_USABLE` treats these as unusable, so those pixels become NaN in both training and inference.

Measured on 52 sites ([CSV](evidence/label_audit_global_ml_ready_52.csv)):

| | Inside labels | Outside labels |
| --- | --- | --- |
| Pooled share of pixels classed cloud (8, 9, 10) | **2.35%** | 0.19% |
| Mean per site | 2.58% | 0.19% |
| Sites with ≥1% cloud inside and ≥2× the outside rate | 24 of 52 | |
| Sites with >5% of labelled pixels classed cloud | 6 of 52 (max 28.1%, gmlr-1270) | |
| Share classed snow (11), the case the code anticipated | 0.07% | 0.60% |

[`evidence/scl_cloud_on_tailings.png`](evidence/scl_cloud_on_tailings.png) shows the three worst sites: the cyan "cloud" pixels follow a tailings facility and pit benches exactly, and there is no real cloud in view.

Why it matters:
- The model never sees the brightest disturbance, and those pixels are dropped from the loss.
- Scene-level coverage still says **pass** (the mine is a small share of the tile), so FR-052 would report a candidate as missing, not as unseen. Bright Jos tin tailings are at risk.

Options, none implemented yet:
1. Treat class 8 as "suspect bright" when the reflectance does not look like cloud (for example blue reflectance below a threshold), like class 11 today. Needs a threshold tested on labelled cloud and on tailings.
2. Use an independent cloud mask (for example s2cloudless or OmniCloudMask) instead of SCL classes 8 and 9.
3. Keep SCL, but report the masked share inside each candidate or region of interest, so masking near mines is visible to the analyst.

Option 3 is cheap and fits FR-052 whichever of 1 or 2 is chosen.

**Decision (2026-11-06): option 3 adopted.** The mask is unchanged. `minefoot mask-report` and the gallery now give each polygon's masked share with a status (`assessed`, `partly_masked` at 5% or more, `mostly_masked` at 50% or more, `outside_image`; thresholds provisional). On gmlr-1270 it reports 28.3% masked (28.1% cloud) and on gmlr-1076 11.4%, matching the audit above. Options 1 and 2 remain open.

### Option 1 checked on 40 sites (2026-11-14): a simple threshold does not separate them

`scripts/cloud_mask_study.py` ([evidence/cloud_mask_study_40.json](evidence/cloud_mask_study_40.json)). No mask was changed. Prepared reflectance blanks these pixels, so the 40 sites were re-fetched (same scenes, Planetary Computer) and the raw bands converted with the preprocessing path's `to_reflectance`, unmasked.

- Across all 180 G1 sites, SCL cloud (classes 8, 9) inside labels totals 671,016 pixels, against 747,575 more than 1 km from any label, an area 14 times larger (634 million against 45 million pixels): 1.49% of labelled pixels against 0.12% far away. 112 sites have over 200 such pixels on mines; 19 have over 5,000 far away.
- The 40 sites: the 19 with most far cloud, plus the 21 with most cloud on mines. Groups (sampled, capped at 20,000 pixels per site): SCL cloud on mines, SCL cloud more than 1 km from labels ("far"), and usable bare ground on mines.

| Rule re-admitting a cloud pixel | On mines | Far | Bare on mines (reference) |
| --- | --- | --- | --- |
| B02 < 0.20 | 0.670 | 0.434 | 0.912 |
| B02 < 0.25 | 0.778 | 0.585 | 0.972 |
| B02 < 0.30 | 0.850 | 0.713 | 0.992 |
| B02/B11 < 0.8 | 0.695 | 0.540 | 0.958 |
| B02 < 0.25 and B02/B11 < 0.8 | 0.527 | 0.401 | 0.933 |

Median B02 is 0.154 on mines and 0.221 far; median B02/B11 is 0.69 and 0.78. The groups overlap heavily.

Why this does not settle option 1:
- The "far" group is not clean cloud. These scenes are almost cloud-free: median scene cloud cover is 0.49%, and 24 of the 40 are below 1%. Those 24 scenes hold 175,056 of the 712,976 far-cloud pixels. Thick cloud usually has blue reflectance well above 0.3, and most far pixels are below it, so much of "far" is probably bright ground away from the labels, which SCL makes the same mistake on.
- So the table cannot tell "the rule re-admits real cloud" from "the rule re-admits bright ground that is not labelled mining". A threshold needs labelled cloud, as option 1 already said.

What this does show:
- On these scenes SCL classes 8 and 9 are mostly not cloud near mines, and probably not far from them either.
- That points to option 2 (an independent cloud mask) or to testing option 1 on a labelled cloud set (for example CloudSEN12). Either needs its licence recorded in `docs/licence_register.csv` first. Founder decision; the mask stays as it is.

Licences read 2026-11-14 (rows in `docs/licence_register.csv`):
- **CloudSEN12+ v1.1.2: CC0 1.0.** Expert cloud and shadow labels on Sentinel-2, with an L2A mode and the Sen2Cor (SCL) mask included per patch, plus WorldCover. It can measure SCL cloud against expert labels directly, including on bright bare ground, and set a threshold for option 1. Its L2A was processed by Google Earth Engine, so check the radiometric offset first.
- **OmniCloudMask 1.8.1** (option 2, red, green and NIR input, L1C or L2A): code and weights MIT. The weights start from ImageNet-pretrained encoders, the same open question as our U-Net encoder, and its training-data terms are not read yet.
- **s2cloudless:** CC BY-SA 4.0, and it needs B01 and B10, which L2A and our input schema lack. Not planned.

Suggested order, founder's call: measure SCL, and any option-1 rule, on CloudSEN12+ L2A patches first (free, public domain). Then test OmniCloudMask on the same patches and on our 40 study sites.

### SCL measured against CloudSEN12+ expert labels (2026-11-14)

`scripts/cloudsen12_scl_study.py` ([evidence/cloudsen12_scl_study.json](evidence/cloudsen12_scl_study.json)). The founder approved the test; no mask was changed. The protocol is in the script and was fixed before measuring:
- Patches with expert labels of type "high", 509 px. 535 validation patches (107 regions) for fitting, 975 test patches (195 regions) measured once. None failed to read.
- Truth is the expert label. "SCL cloud" means classes 8 and 9, the ones our mask drops. Results are broken down by WorldCover class.
- Intervals are 95%, from resampling regions.

On test:

| Measure | Value (95% interval) |
| --- | --- |
| Expert-clear pixels SCL calls cloud, all land cover | 3.9% (2.5–5.5%) |
| on bare / sparse vegetation | **7.9% (3.8–12.8%)** |
| on built-up | 8.4% (3.2–14.4%) |
| on trees | 2.3% (1.1–3.8%) |
| on crops | 3.0% (0.6–6.4%) |
| on water | 0.7% (0.3–1.3%) |
| Expert-clear pixels SCL calls cirrus (class 10, not masked by us) | 6.6% (5.0–8.2%) |
| SCL-cloud pixels that experts call clear | 8.7% (5.7–11.5%) |
| Expert thick cloud that SCL puts in classes 8 or 9 | 63.2% (60.5–66.0%) |

So SCL's false cloud on clear ground is about three times as common on bare ground and built-up areas as on trees. That matches what we saw on mines (1.49% of labelled mining pixels against 0.12% far away, on nearly cloud-free scenes).

**Option 1 rule, fitted on validation, measured on test.** The rule re-admits an SCL-cloud pixel when B02 < t. The fitted value is the largest t with at most 1% of expert thick cloud re-admitted on validation: **t = 0.12**. On test:
- Thick cloud re-admitted: 0.69% (0.42–1.00%).
- Thin cloud re-admitted: 11.5% (6.2–18.4%).
- False cloud on clear ground recovered: 18.6% (10.4–32.2%), and on bare ground 19.1% (10.3–26.6%).

On our 40 mining sites, the same cut re-admits 29.0% of SCL-cloud pixels on mines and 14.3% of those more than 1 km away (the table above, now with 0.12).

What this means:
- A blue cut safe for thick cloud recovers only about a fifth of SCL's false cloud, and about 29% on our mines. It also lets through about one in nine thin-cloud pixels.
- It would help, but it is not a fix: most bright mine ground that SCL calls cloud has B02 above 0.12, where real cloud also lies.
- The mask-report thresholds (option 3) stay necessary whatever is chosen.
- Indicative only for current SCL: these patches are from 2018–2020, processed by Google Earth Engine with older Sen2Cor versions, while our Nigerian scenes are baseline 05.xx.
- Next options, founder's call:
  - adopt the B02 < 0.12 re-admit as a labelled "suspect bright" class, as class 11 (snow) is handled today;
  - test OmniCloudMask on these test patches and our 40 sites. Check first whether CloudSEN12 is in its training data, which would make this test optimistic.

### OmniCloudMask (option 2) against the same expert labels (2026-11-14)

`scripts/cloudsen12_ocm_study.py` ([evidence/cloudsen12_ocm_study.json](evidence/cloudsen12_ocm_study.json)). The founder approved the test; no mask was changed. The protocol is in the script and was fixed before running:
- OmniCloudMask 1.8.1, model 4.0, the two-model default. Inputs are B04, B03 and B8A L2A reflectance. Weight SHA-256 hashes are in the evidence.
- Same 975 test patches as the SCL study, with nothing fitted. None failed.
- Training overlap was checked first, on its published training-data map. Its CloudSEN12 "high" count is exactly twice the CloudSEN12+ training-split count in every one of 2,158 tiles (L1C plus L2A), so it never saw these test patches. Its other training sets touch the tiles of 243 test patches. The "clean" subset (732 patches, 151 regions) has no training data of any kind in its tiles.
- Compared on the same pixels; paired 95% intervals come from resampling regions.

All 975 test patches:

| | SCL classes 8, 9 | OCM thick | OCM thick or thin |
| --- | --- | --- | --- |
| Expert-clear pixels called cloud | 3.9% | **1.2%** (1.1–1.4%) | 3.4% (2.9–4.0%) |
| on bare / sparse | 7.9% | **1.0%** (0.7–1.3%) | 4.0% (2.8–5.4%) |
| on built-up | 8.4% | 0.9% (0.6–1.3%) | 3.1% |
| on trees | 2.3% | 0.9% (0.8–1.1%) | 3.2% |
| on water | 0.8% | 1.8% (1.2–2.4%) | 5.1% |
| Expert thick cloud caught | 63.2% | **93.3%** (92.1–94.3%) | 95.9% |
| Expert thin cloud caught | 32.8% | 12.0% | 80.1% |

Paired, OCM thick minus SCL:
- False cloud on clear: −2.8 points (−4.2 to −1.3).
- On bare ground: −6.9 points (−11.9 to −2.8).
- On built-up: −7.4 points (−13.2 to −2.3).
- Thick cloud caught: +30.1 points (+27.4 to +32.6).

The clean subset gives the same picture: on bare ground SCL 7.9% against OCM thick 1.1%, paired −6.9 points (−12.6 to −2.1); thick cloud caught 61.9% against 92.9%.

What this means:
- At the thick-cloud setting, OmniCloudMask calls clear bare ground cloud about eight times less often than SCL does, and still catches far more thick cloud. That is the trade we want for bright mine ground.
- Two costs:
  - OCM thick misses most thin cloud. That is no worse for us than SCL, whose class 10 (cirrus) we do not mask.
  - Its false cloud on clear water is about twice SCL's (1.8% against 0.8%). Tailings ponds and pit lakes are water.
- Still to check:
  - These patches are from 2018–2020; our Nigerian scenes are baseline 05.xx.
  - The weights are MIT-licensed but start from ImageNet-pretrained encoders, the same commercial-use question as our U-Net encoder.
  - It costs about 4 s per 512 px patch on two CPU threads.

**On our 40 mining sites** (`scripts/ocm_mining_sites.py`, [evidence/ocm_mining_sites_40.json](evidence/ocm_mining_sites_40.json)). These are the same sites as the option-1 check, re-fetched one at a time with raw files deleted after use. There are no cloud labels, and the scenes are nearly cloud-free.

| | SCL classes 8, 9 | OCM thick | OCM thick or thin |
| --- | --- | --- | --- |
| Labelled mine pixels called cloud | 4.47% | **0.28%** | 0.51% |
| Pixels more than 1 km from labels called cloud | 0.53% | 1.61% | 2.71% |
| Sites with more than 5% of mine pixels called cloud | 10 of 40 | **0 of 40** | 1 of 40 |

OCM calls clear 99.3% of the mine pixels SCL calls cloud. In the 24 scenes below 1% scene cloud, OCM thick calls 0.10% of mine pixels cloud against SCL's 4.69%.

Away from mines OCM marks more cloud than SCL. Most of it is in the cloudier scenes (6–9% scene cloud), which fits CloudSEN12+, where SCL missed about a third of thick cloud. The one near-clear scene with much far cloud is gmlr-1090 (scene cloud 0.13%, OCM 4.5% of far pixels). Its picture shows the marked areas are visible thin cloud along ridges and a hazy patch, which SCL missed.

Pictures (true colour; labels yellow; SCL cloud cyan in the middle; OCM thick magenta on the right):
- [evidence/ocm_vs_scl_gmlr-1076.png](evidence/ocm_vs_scl_gmlr-1076.png): SCL marks the bright mine ground, OCM marks none.
- [evidence/ocm_vs_scl_gmlr-1090.png](evidence/ocm_vs_scl_gmlr-1090.png): OCM marks real cloud and haze that SCL misses.

**Summary for the founder decision.** On expert labels and on our mines, OmniCloudMask at the thick-cloud setting does what option 1 could not. It almost never calls bright mine ground cloud, and it catches more real cloud. Open before adopting it:
- the commercial question on its ImageNet-pretrained encoders and its training data (KappaSet, unreleased sets);
- its higher false cloud on clear water;
- a check on current-baseline Nigerian scenes (after the test lock);
- the compute cost.

The decision is the founder's. Neither the mask nor the masking report has been changed.

## Overlaps and splits (2026-11-06)

`minefoot overlaps` on the 1,207 sites found 985 pairs: 430 sharing a scene, 306 with overlapping tile footprints and 249 with overlapping labels. All are within this one dataset; cross-dataset checks start when a second dataset is ingested.

**The authors' split keeps overlapping tiles together but not nearby ones.** `minefoot check-split --split-column source_split`: no footprint or label overlap crosses their splits, but 21 pairs of sites in different splits are within 10 km of each other, and 94 pairs cut from the same scene sit in different splits. [`evidence/split_authors_vs_project_ghana.png`](evidence/split_authors_vs_project_ghana.png) shows val and test tiles a few kilometres from training tiles in the Ghana cluster.

**Project split, v1.** `minefoot split` with 50 km blocks, a 10 km buffer, sites sharing a scene joined, stratified by mine scale, seed 0: 845 train, 181 val and 181 test, in 795 units (the largest has 19 sites). Artisanal sites are 19% of each split. `check-split` finds no leakage. Assignments: [`evidence/split_global_ml_ready_v1.csv`](evidence/split_global_ml_ready_v1.csv); summary: [`evidence/split_global_ml_ready_summary.json`](evidence/split_global_ml_ready_summary.json). Its test set is **not locked**. Lock it with `minefoot lock-test` once the baseline plan is agreed. This test set is for development only: Nigerian performance can only come from the locked Nigerian set.

## Attribution

Evidence images contain modified Copernicus Sentinel data 2016 to 2019. Label outlines are from the Global ML-ready dataset (Jasansky, Maus, Popa, Wilbik), CC BY-SA 4.0, derived from Maus et al. v2 and Tang and Werner.
