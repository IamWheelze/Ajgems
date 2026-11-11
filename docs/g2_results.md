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

## Follow-up, 2026-11-08: false-candidate definition and hard negatives

**False-candidate definition (v2).** A predicted patch of 1 ha or more now counts as a false candidate when under half of its usable pixels are labelled mining. Before, it counted only if it touched no label at all, so large false areas attached to a mine were missed. The old count is kept as `false_candidates_detached`, and false-candidate area is now reported too. Both runs were rescored from their stored score maps at their original thresholds (`minefoot rescore`; previous files kept as `metrics_before_rescore_1.json`).

Counts barely moved (G2 validation 27 → 29 per 1,000 km²; G1 random forest 154 → 159). A large false area is still one patch, so **area is the measure that tells the models apart**. False-candidate area on validation: U-Net 2,900 ha against the random forest's 2,941 per 1,000 km², no clear difference (paired).

**Hard negatives mined from the model's own errors.** `minefoot mine-negatives` ran `g2_v1` over the 60 **training** sites only, at its validation-chosen threshold. It kept every flagged patch of 1 ha or more beyond 500 m of any label: 782 patches, 31,360 ha ([summary](evidence/g2_hn/mined_negatives_summary.json); main WorldCover class tree 280, grass 174, bare 97, built-up 83, water 77). `g2_hn` fine-tuned `g2_v1`'s checkpoint for 12 epochs at learning rate 3e-4, with 25% of chips centred on these negatives (best epoch 6; threshold 0.41, chosen on validation; commit `2b6ecb5`; [config](evidence/g2_hn/config.json)).

Paired, `g2_hn` minus `g2_v1` ([val](evidence/g2_hn/paired_vs_g2_v1_val.json), [test](evidence/g2_hn/paired_vs_g2_v1_test.json)):

| Difference | Validation | Test |
| --- | --- | --- |
| Pixel F1 | +0.028 (−0.014 to +0.062), unclear | +0.053 (+0.004 to +0.100), better |
| Precision | +0.076 (+0.024 to +0.105), better | +0.074 (+0.013 to +0.149), better |
| Recall | −0.042 (−0.133 to +0.026), unclear | −0.013 (−0.038 to +0.009), unclear |
| Footprints covered ≥25% | +0.008 (−0.037 to +0.051), unclear | −0.038 (−0.074 to +0.000), unclear |
| False-alarm km² per 1,000 km² | **33.3 → 22.9: −10.4 (−21.1 to −1.7), better** | 64.7 → 46.0: −18.7 (−41.0 to −2.9), better |
| False-candidate ha per 1,000 km² | 2,900 → 1,984: −916 (−1,869 to −95), better | 6,591 → 4,731: −1,860 (−4,060 to −201), better |

**Decision (validation): `g2_hn` is the current best model.** It wrongly flags about a third less ground at the same recall. Against the G1 random forest, its validation F1 is +0.237 (+0.145 to +0.320), and its test false-alarm area is lower by 16 km² per 1,000 km² (−34.9 to −1.0). Test pixel F1 is 0.479 (0.383 to 0.593): artisanal 0.57, industrial 0.45, Africa 0.43. Per-type false alerts on validation fell for riverbank (17.5% → 11.6%) and bare ground (18.1% → 9.5%) ([file](evidence/g2_hn/false_alerts_by_type.json)). Error gallery: [`results/g2_hn_unet_errors.html`](../results/g2_hn_unet_errors.html).

Caveats:
- **The mined negatives are the model's own errors beyond 500 m of labels.** Labels are incomplete, so some may be real unlabelled mining. They help on these metrics, but a reviewer should look at a sample before they are reused.
- **This is a second experiment at this gate,** decided on validation. The test set was scored for the record only.

Digitised hard negatives use the same route: `minefoot rasterize-negatives hard_negatives.gpkg --sites … --out <folder>` adds them to the masks, then `minefoot g2 --negatives-root <folder>`.

## Precision-recall and reliability of `g2_hn` (2026-11-11)

`minefoot curves data/runs/g2_hn …` pooled the stored score maps per split ([`curves.json`](evidence/g2_hn/curves.json)). At the run's threshold, the recomputed pixel counts equal the stored per-site counts exactly: validation precision 0.536, recall 0.576; test 0.404 and 0.588. The web app's run dashboard (FR-130) charts this file.

**The model score is not a probability, and it is overconfident.** Share of pixels that are labelled mining, by score bin:

| Score bin | Validation | Test |
|---|---|---|
| 0.0–0.1 | 0.014 | 0.016 |
| 0.4–0.5 | 0.297 | 0.279 |
| 0.7–0.8 | 0.442 | 0.359 |
| 0.9–1.0 | 0.727 | 0.477 |

Scores above about 0.2 overstate the labelled share, and more on test than on validation. Incomplete labels explain part of this (unlabelled mining counts against the model), so it is not a pure calibration error. "Model score" wording therefore stays (FR-084). Calibration needs labels checked for completeness, such as the Nigerian sites under the 1 km protocol.

## What this means

1. **G2 passed, so G3 is next:** the first test on Nigerian sites with no Nigerian training. It needs the founder's 30 to 50 digitised Nigerian sites, locked first (step 7). It will measure the gap between global and Nigerian performance.
2. **Large false areas in dry landscapes and water are the main failure.** Model-mined hard negatives cut false-alarm area by about a third (above). Digitised negatives (bare land, dry stream beds, water bodies near mines) should follow.
3. **False-candidate definition fixed** (v2, above). Read the count together with false-candidate area and false-alarm area.
4. **The artisanal recall (0.43) is still limited by loose labels**, which enclose forest. Tighter Nigerian labels will show the real figure.
5. **The pretrained encoder's licence must be resolved before release**, or the model retrained with `--encoder-weights none`.
6. **The web app may now start** under CLAUDE.md's rule "not until G2 has passed". It must carry the FR-052 masking report, "model score" wording and the FR-110 limits.
