# Gate G2: U-Net results (2026-11-08)

**Internal research only**, on Global ML-ready (CC BY-SA 4.0; legal review of share-alike still open). Nothing here measures Nigerian performance; that is gate G3. All numbers were measured in run `g2_v1`. Scores are model scores, not probabilities.

## Decision

**G2 passes.** FRD 8.3 requires G2 to beat G1 at site level by more than the confidence interval. On validation, the paired site-bootstrap difference in pixel F1 between the U-Net and the G1 random forest is **+0.208 (95% interval +0.147 to +0.282)**, wholly above zero. The test comparison agrees: **+0.197 (+0.125 to +0.295)**.

**The gain is in finding mining, not in avoiding false alarms.** Recall rises by +0.29 and precision by +0.15. The area wrongly flagged is about the same as G1's: 33 against 34 km² per 1,000 km² on validation, and 65 against 62 on test, with no clear difference either way. The U-Net concentrates its false alarms into a few large areas instead of many small patches.

## What was run

`minefoot g2` at commit `80beb38` on CPU in the cloud session (lineage in [`evidence/g2_v1/config.json`](evidence/g2_v1/config.json)). The container restarted twice during training; the run resumed after epochs 14 and 32 from saved state, and both resumes are recorded in the config.

- **Sites:** the same 60 train, 60 validation and 60 test sites as G1 (`evidence/g1_v1/sites.json`), from split v1, whose test set is locked.
- **Model:** U-Net with a ResNet-34 encoder (ImageNet-pretrained; the weights' licence is not verified, see the licence register). Input is the 10 prepared bands, normalised with statistics from training sites only ([`normalisation.json`](evidence/g2_v1/normalisation.json)).
- **Training:** 40 epochs of 32 random 256 × 256 chips per training site (half centred on mining), flips and rotations, AdamW 1e-3 with cosine decay, batch 16. Loss is binary cross-entropy plus Dice, with masked pixels excluded.
- **Checkpoint:** lowest loss on fixed validation chips, at **epoch 24** ([`history.csv`](evidence/g2_v1/history.csv)). Training loss kept falling to 0.39 while validation-chip loss bottomed at 0.64, so later epochs overfit.
- **Scoring:** whole tiles with 64-pixel tapered overlap. Score threshold 0.74, chosen on validation by pooled pixel F1. Test was scored once.
- **Evaluation:** the same code as G1 (`baseline.evaluate_scores`).
- **No hyperparameter search.** This is one configuration.

## Results

Pixel metrics with 95% site-bootstrap intervals:

| | Precision | Recall | Pixel F1 | IoU | Footprints covered ≥25% | False-alarm km² per 1,000 km² |
| --- | --- | --- | --- | --- | --- | --- |
| Validation, U-Net | 0.460 | 0.618 | 0.527 (0.435–0.618) | 0.358 | 0.53 | 33.3 |
| Validation, G1 random forest | 0.307 | 0.332 | 0.319 | 0.190 | 0.43 | 34.3 |
| Test, U-Net | 0.330 | 0.600 | 0.426 (0.335–0.563) | 0.270 | 0.74 | 64.7 |
| Test, G1 random forest | 0.193 | 0.280 | 0.229 | 0.129 | 0.41 | 62.0 |

Paired U-Net minus G1 random forest ([val](evidence/g2_v1/paired_vs_g1_rf_val.json), [test](evidence/g2_v1/paired_vs_g1_rf_test.json)):

| Difference | Validation | Test |
| --- | --- | --- |
| Pixel F1 | **+0.208 (+0.147 to +0.282)** | +0.197 (+0.125 to +0.295) |
| Precision | +0.153 (+0.050 to +0.264) | +0.137 (+0.056 to +0.266) |
| Recall | +0.286 (+0.240 to +0.334) | +0.321 (+0.277 to +0.361) |
| Footprints covered ≥25% | +0.096 (−0.027 to +0.217), unclear | +0.332 (+0.249 to +0.417) |
| False-alarm area | −1.0 (−18.5 to +10.6), unclear | +2.7 (−21.0 to +28.7), unclear |

Validation by stratum, paired F1 difference (files `evidence/g2_v1/paired_vs_g1_rf_val_*.json`):

| Stratum | Sites | Random forest F1 | U-Net F1 | Difference |
| --- | --- | --- | --- | --- |
| Artisanal | 20 | 0.148 | 0.454 | +0.307 (+0.233 to +0.382) |
| Industrial | 40 | 0.358 | 0.549 | +0.191 (+0.118 to +0.272) |
| Africa | 27 | 0.289 | 0.475 | +0.187 (+0.108 to +0.289) |

Test by stratum (U-Net): artisanal F1 0.55 (precision 0.75, recall 0.43); industrial 0.39 (0.27, 0.72); Africa 0.38 (0.25, 0.76). All strata: [`evidence/g2_v1/metrics.json`](evidence/g2_v1/metrics.json).

## Where it fails

[`results/g2_v1_unet_errors.html`](../results/g2_v1_unet_errors.html) shows the 15 worst test sites. On test, 56% of the U-Net's false-alarm pixels are in three sites:

- **gmlr-1513:** most of an arid basin and its town.
- **gmlr-98:** an entire lake beside open-pit workings.
- **gmlr-1440:** desert ground.

At gmlr-1105 most of a dry landscape is flagged. These large false areas touch a labelled mine, so the **false-candidate count misses them**. Its definition counts only patches touching no label: the U-Net's 29 per 1,000 km² against the random forest's 272 looks like a large improvement, but the area figure shows it is not. False-alarm area is now a standard metric (`false_alarm_km2_per_1000km2`) and is reported by `minefoot compare`.

Per-type false-alert rates on validation (WorldCover proxy types, [file](evidence/g2_v1/false_alerts_by_type.json)): farmland 2.0%, settlement 6.3%, riverbank 17.5%, bare ground 18.1%, other land 1.1%. Compared with the random forest (0.7%, 14.2%, 15.8%, 27.2%, 0.6%), the U-Net flags less settlement and bare ground but more farmland, riverbank and other land.

## What this means

1. **G2 passed, so G3 is next:** the first test on Nigerian sites with no Nigerian training. It needs the founder's 30 to 50 digitised Nigerian sites, locked first (step 7). It will measure the gap between global and Nigerian performance.
2. **Large false areas in dry landscapes and water are the main failure.** Before any release, add digitised hard negatives (bare land, dry stream beds, water bodies near mines), and consider a minimum-confidence rule for very large predicted areas.
3. **Fix the false-candidate definition.** Count predicted patches that are mostly outside labels, not only those touching none, so large attached false areas are counted. Until then, read false-alarm area alongside it.
4. **The artisanal recall (0.43) is still limited by loose labels**, which enclose forest. Tighter Nigerian labels will show the real figure.
5. **The pretrained encoder's licence must be resolved before release**, or the model retrained with `--encoder-weights none`.
6. **The web app may now start** under CLAUDE.md's rule "not until G2 has passed". It must carry the FR-052 masking report, "model score" wording and the FR-110 limits.
