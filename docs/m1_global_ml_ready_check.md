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

## Attribution

Evidence images contain modified Copernicus Sentinel data 2016 to 2019. Label outlines are from the Global ML-ready dataset (Jasansky, Maus, Popa, Wilbik), CC BY-SA 4.0, derived from Maus et al. v2 and Tang and Werner.
