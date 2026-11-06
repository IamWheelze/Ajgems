# Gate G1 baselines: results (2026-11-06)

**Internal research only.** Trained and tested on Global ML-ready (CC BY-SA 4.0, legal review of share-alike still open). Nothing here says anything about Nigerian performance; only the locked Nigerian test set can (gate G3). All numbers below were measured in run `g1_v1`; scores are model scores, not probabilities.

## What was run

`minefoot g1` at commit `7206f95` (lineage in [`evidence/g1_v1/config.json`](evidence/g1_v1/config.json)).

- **Sites.** 60 train, 60 validation and 60 test from project split v1 (50 km blocks, 10 km buffer, stratified by mine scale; split file SHA-256 `6144e9a4…`). Sites were sampled with Africa weighted up. Test: 20 artisanal and 40 industrial; 22 in Africa.
- **Index threshold.** Three candidates: bare-soil index (BSI), built-up index (NDBI) and negative NDVI. The index and its threshold were chosen by pooled pixel F1 on validation.
- **Random forest.** 100 trees, depth 20, fitted on 360,000 pixels (3,000 mining and 3,000 other per training site), using 10 bands and 5 indices. Its score threshold was chosen on validation. No hyperparameter search.
- **Test.** Scored once at the validation-chosen settings, after which its 181 sites were locked ([`evidence/test_lock_global_ml_ready_v1.json`](evidence/test_lock_global_ml_ready_v1.json)).
- **Masked pixels** are excluded from all metrics. They hid 1.0% of labelled test pixels.
- **Footprints** are connected parts of the label of at least 1 ha. "Found" means any predicted pixel inside; "covered" means at least 25% predicted. A false candidate is a predicted patch of at least 1 ha touching no label.
- **Intervals** are 95% site-bootstrap intervals (1,000 resamples).

## Validation (choosing)

| Model | Pixel F1 at chosen threshold |
| --- | --- |
| BSI threshold | 0.115 |
| NDBI threshold | 0.111 |
| **Negative NDVI threshold** (chosen index) | 0.230 |
| **Random forest** (score ≥ 0.84) | 0.319 |

## Test (60 sites, scored once)

| Model | Precision | Recall | Pixel F1 | IoU | Footprints covered ≥25% | False candidates per 1,000 km² |
| --- | --- | --- | --- | --- | --- | --- |
| Negative NDVI | 0.150 (0.097–0.269) | 0.227 (0.166–0.295) | 0.181 (0.135–0.241) | 0.099 | 0.35 | 155 |
| Random forest | 0.193 (0.132–0.323) | 0.280 (0.220–0.347) | 0.229 (0.178–0.291) | 0.129 | 0.41 | 272 |

"Found with any overlap" is 0.95 for the index and 0.99 for the random forest. Both models flag so much ground that almost every footprint is touched, so this number says little; the 25% coverage figure is more informative.

Random forest, test, by stratum (pixel F1 with interval; footprints covered ≥25%):

| Stratum | Sites | Precision | Recall | F1 | Covered ≥25% |
| --- | --- | --- | --- | --- | --- |
| Artisanal | 20 | 0.59 | 0.12 | 0.20 (0.16–0.26) | 0.15 |
| Industrial | 40 | 0.17 | 0.39 | 0.24 (0.17–0.32) | 0.53 |
| Africa | 22 | 0.17 | 0.44 | 0.24 (0.18–0.36) | 0.50 |
| Americas | 12 | 0.52 | 0.23 | 0.32 (0.22–0.42) | 0.35 |
| Asia and Oceania | 13 | 0.30 | 0.21 | 0.25 (0.18–0.33) | 0.29 |
| Europe and north Asia | 13 | 0.13 | 0.21 | 0.16 (0.09–0.30) | 0.43 |

All strata for both models: [`evidence/g1_v1/metrics_summary.json`](evidence/g1_v1/metrics_summary.json). Per-site counts: [`evidence/g1_v1/per_site.csv`](evidence/g1_v1/per_site.csv).

## What it means

1. **G1's condition is met**: metrics are recorded per stratum on held-out sites. Performance is low, as expected for pixel baselines.
2. **The random forest is better than the index but not clearly.** Its test F1 interval (0.18–0.29) overlaps the index's (0.14–0.24). These intervals are not paired, so this does not test the difference.
3. **False candidates dominate.** 272 per 1,000 km² for the random forest, against FRD objective O2's 5 before review. [`evidence/g1_v1/false_candidates_rf.png`](evidence/g1_v1/false_candidates_rf.png) shows the worst two test sites: the false candidates (orange) are bare cultivated fields and dry, bare slopes and stream beds, not unlabelled mines. These are the hard negatives of FR-074 (farmland, riverbanks), and none are in the training labels as negatives.
4. **Artisanal recall is very low (0.12) while precision is high (0.59).** This matches the loose artisanal outlines seen earlier: they enclose forest between workings, so much of the "missed" area is vegetation the model correctly leaves alone. Artisanal recall here understates true detection. It needs tighter labels to measure.
5. **Label omission inflates false candidates somewhat.** Maus v2 reports producer's accuracy of 78.9% for mines, so some orange patches near mines may be real disturbance. The fields and stream beds in the figure are not.

## For G2 (U-Net)

- **The bar.** G2 must beat this random forest at site level by more than the confidence interval (FRD 8.3). Compare on the same 60 test sites using a **paired** site bootstrap, which `summarise` does not do yet.
- **Hard negatives.** Add typed hard negatives (farmland, riverbanks, bare slopes) before or with G2. Spatial context should help here, but only labelled negatives will show it.
- **Artisanal labels.** Report artisanal sites separately, and do not tune on their recall until the labels are tightened.

## Reproduce

Site lists are in `config.json`.

```bash
minefoot g1 --sites data/g1/sites.json --split data/splits/gmlr_split.gpkg \
  --prepared-root data/prepared/global_ml_ready --labels-root data/raw/global_ml_ready --out data/runs/g1_v1
```
