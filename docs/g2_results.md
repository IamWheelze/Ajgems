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

`g2_v1` is more overconfident still (top bin 0.52 validation, 0.35 test; [`curves.json`](evidence/g2_v1/curves.json)). Scores above about 0.2 overstate the labelled share, and more on test than on validation. Incomplete labels explain part of this (unlabelled mining counts against the model), so it is not a pure calibration error. "Model score" wording therefore stays (FR-084). Calibration needs labels checked for completeness, such as the Nigerian sites under the 1 km protocol.

## What this means

1. **G2 passed, so G3 is next:** the first test on Nigerian sites with no Nigerian training. It needs the founder's 30 to 50 digitised Nigerian sites, locked first (step 7). It will measure the gap between global and Nigerian performance.
2. **Large false areas in dry landscapes and water are the main failure.** Model-mined hard negatives cut false-alarm area by about a third (above). Digitised negatives (bare land, dry stream beds, water bodies near mines) should follow.
3. **False-candidate definition fixed** (v2, above). Read the count together with false-candidate area and false-alarm area.
4. **The artisanal recall (0.43) is still limited by loose labels**, which enclose forest. Tighter Nigerian labels will show the real figure.
5. **The pretrained encoder's licence must be resolved before release**, or the model retrained with `--encoder-weights none`.
6. **The web app may now start** under CLAUDE.md's rule "not until G2 has passed". It must carry the FR-052 masking report, "model score" wording and the FR-110 limits.

## Retrain without pretrained weights: `g2_nw` and `g2_nw_hn` (2026-11-14)

**Why:** the ImageNet encoder weights carry no commercial grant (licence register; `docs/legal_review_brief.md`, question 2). This retrain removes them from the lineage.

**What was run.** Both runs were on CPU in the cloud session, with the same sites, split, preprocessing and evaluation as `g2_hn`. `data/runs/run_g2_nw.sh` then `eval_g2_nw.sh` ran them.

| Run | Same as | Except | Kept checkpoint |
| --- | --- | --- | --- |
| `g2_nw` | `g2_v1` | `--encoder-weights none`; 60 epochs instead of 40, to give random initialisation more time. Resumed from saved state after container restarts, each recorded in its config. | Epoch 10, validation-chip loss 0.646. Later epochs overfit: training loss fell to 0.44 while validation-chip loss rose to 0.77–0.82. |
| `g2_nw_hn` | `g2_hn` | Hard negatives mined from `g2_nw`'s own errors on training sites; fine-tuned from `g2_nw`'s checkpoint for 12 epochs at learning rate 3e-4. | Epoch 2. |

Validation chose a threshold of 0.77 for `g2_nw` and 0.53 for `g2_nw_hn`.

**Lineage.** `g2_nw_hn` was made at commit `7df37c1`, and its `pretrained_lineage` is empty: no pretrained weights anywhere in its ancestry. `g2_nw` was started two minutes before commit `1f75d1e`, so its config says `d381256+uncommitted`. The only uncommitted change was the code that writes `pretrained_lineage` into the config, which does not affect training. The registry still refuses `g2_nw` under its reproducibility rule. That leaves `g2_nw_hn` as the registrable model without pretrained weights.

**Results** (pixel F1 with 95% site-bootstrap intervals; evidence in [`evidence/g2_nw`](evidence/g2_nw) and [`evidence/g2_nw_hn`](evidence/g2_nw_hn)):

| | Validation F1 | Test F1 | Test precision | Test recall | Test false-alarm km² per 1,000 km² |
| --- | --- | --- | --- | --- | --- |
| `g2_hn` (ImageNet encoder, current best) | 0.555 (0.471–0.636) | 0.479 (0.383–0.593) | 0.404 | 0.588 | 46.0 |
| `g2_nw_hn` (no pretrained weights) | 0.502 (0.408–0.600) | 0.420 (0.321–0.538) | 0.341 | 0.548 | 56.3 |
| `g2_nw` (no pretrained weights, before hard negatives) | 0.486 (0.391–0.582) | 0.383 (0.296–0.493) | 0.286 | 0.582 | 77.2 |
| G1 random forest | 0.319 | 0.229 | 0.193 | 0.280 | 62.0 |

**Paired comparisons** (same sites, B − A, 95% interval):

| B against A | Validation F1 | Test F1 | Other clear differences |
| --- | --- | --- | --- |
| `g2_nw_hn` vs `g2_hn` | −0.054 (−0.113 to +0.022), no clear difference | −0.059 (−0.102 to −0.001), worse | Test: recall −0.040 (−0.068 to −0.010), false candidates +10.7 per 1,000 km² (+2.6 to +20.0) |
| `g2_nw_hn` vs G1 random forest | +0.183 (+0.134 to +0.239), better | +0.192 (+0.129 to +0.273), better | Recall and precision better on both sets |
| `g2_nw` vs `g2_v1` | −0.042 (−0.097 to +0.005), no clear difference | −0.043 (−0.128 to +0.030), no clear difference | False candidates worse on both sets (validation +13.9, test +13.3 per 1,000 km²) |
| `g2_nw_hn` vs `g2_nw` | +0.016 (−0.014 to +0.051), no clear difference | +0.037 (+0.003 to +0.086), better | Validation false-alarm area −10.0 (−18.3 to −2.6) km² per 1,000 km²; recall −0.046 (−0.076 to −0.018) |

On test strata (no paired intervals per stratum), `g2_nw_hn` against `g2_hn`:
- Artisanal: F1 0.46 against 0.57; footprints covered ≥25% 0.54 against 0.70.
- Industrial: F1 0.41 against 0.45; footprints covered ≥25% 0.78 against 0.70.

**Calibration** (`minefoot calibrate`, [`calibration.json`](evidence/g2_nw_hn/calibration.json)): not calibrated, the same pattern as `g2_hn`. On test, ECE over pixels predicted ≥ 0.1 is 0.293 raw and 0.129 after Platt (0.021–0.250). Platt makes artisanal sites worse (0.117 raw against 0.249).

**What this means.**
1. **Removing the pretrained weights costs accuracy, but not all of G2's gain.** `g2_nw_hn` still beats G1 by a margin well beyond its interval, so the G2 criterion holds without pretrained weights. Against `g2_hn` it is worse on test by about 0.06 F1 (interval just below zero) and no clearly different on validation, where decisions are made. The loss is mostly in artisanal sites.
2. **Hard-negative mining helps the no-pretrained model in the same way as before:** less false-alarm area and better precision, at a small cost in recall.
3. **For commercial use, `g2_nw_hn` is the only registered candidate whose weights have no open licence question.** The share-alike question on the Global ML-ready labels (question 1 of the legal brief) still applies to it. `g2_hn` stays the best model for internal research.
4. **Ways to close the gap without ImageNet weights**, each needing its own paired comparison:
   - longer training with stronger augmentation; the overfit from epoch 10 suggests regularisation matters more than epochs;
   - encoder weights pretrained on satellite imagery under an explicit permissive licence;
   - more labelled data, which the Nigerian labels and the QC pool will add.

## Planned before training: Sentinel-2-pretrained encoder, `g2_s2` and `g2_s2_hn` (declared 2026-11-14)

**Question.** Does an encoder pretrained on Sentinel-2 under an explicit commercial-friendly licence close the gap between `g2_nw_hn` (no pretrained weights) and `g2_hn` (ImageNet weights)?

**Weights.** SSL4EO-S12 MoCo, ResNet-18. They are released under CC BY 4.0 according to the SSL4EO-S12 README and the Hugging Face card, both read 2026-11-14 and recorded in the licence register. The file is pinned by SHA-256. How they are adapted to our inputs:
- They were pretrained on Sentinel-2 L1C, 13 bands, DN / 10000.
- We keep the filters of our 10 L2A bands and fold the training-site normalisation into the first layer.
- ResNet-18 rather than ResNet-34, because no ResNet-34 Sentinel-2 weights are published. The encoder size therefore differs from the other runs, and the comparison cannot separate pretraining from encoder size.

**Runs.** Both are made from commit `ab3e065` (the code of `ab52650` plus this plan) by `data/runs/run_g2_s2.sh`, with the same sites, split, preprocessing and evaluation as before.

| Run | Same as | Except |
| --- | --- | --- |
| `g2_s2` | `g2_v1` (40 epochs) | `--encoder resnet18 --encoder-weights ssl4eo-s12-moco` |
| `g2_s2_hn` | `g2_hn` | Hard negatives mined from `g2_s2`'s own errors; fine-tuned for 12 epochs at learning rate 3e-4 |

**Decision rule, fixed now.**
- The primary comparison is paired validation pixel F1 of `g2_s2_hn` against `g2_nw_hn` and against `g2_hn`. Test is reported, not used to decide.
- `g2_s2_hn` replaces `g2_nw_hn` as the commercial candidate if all three hold:
  - against `g2_nw_hn`, its validation F1 difference has an interval wholly above −0.02 and its point estimate is above zero;
  - its validation false-alarm area is not clearly worse than `g2_nw_hn`'s;
  - the licence register still shows CC BY 4.0 for the weights.
- Otherwise `g2_nw_hn` stays the commercial candidate.
- Either way, `g2_hn` stays the internal-research reference until the ImageNet question is answered.

## Results: `g2_s2` and `g2_s2_hn` (2026-11-14)

All numbers are measured on the same 60 validation and 60 test sites as the earlier runs. Intervals are 95% from resampling sites. Evidence: `docs/evidence/g2_s2/`, `docs/evidence/g2_s2_hn/`, and the rule applied by `scripts/g2_s2_decision.py` in `docs/evidence/g2_s2_hn/decision.json`.

**Training.** `g2_s2` kept epoch 23 of 40 (validation-chip loss 0.613). `g2_s2_hn` kept epoch 1 of its 12-epoch fine-tune (0.712); validation-chip loss rose after that.

| Run | Val F1 | Test F1 | Test precision | Test recall | Test false-alarm km² per 1,000 km² |
| --- | --- | --- | --- | --- | --- |
| `g2_s2` | 0.569 (0.499–0.629) | 0.420 (0.324–0.545) | 0.334 | 0.565 | 59.9 |
| `g2_s2_hn` | 0.502 (0.394–0.615) | 0.415 (0.320–0.541) | 0.324 | 0.577 | 64.1 |
| `g2_nw_hn` (reference) | 0.502 | 0.420 | 0.341 | 0.548 | 56.3 |
| `g2_hn` (reference) | 0.555 | 0.479 | 0.404 | 0.588 | 46.0 |

**Decision rule, as declared: `g2_s2_hn` does not replace `g2_nw_hn`.**
- F1 against `g2_nw_hn` on validation: +0.000 (−0.042 to +0.057). The interval's lower bound is below −0.02, so the first condition fails.
- False-alarm area against `g2_nw_hn` on validation: +5.0 (−6.4 to +17.1), not clearly worse; the condition holds.
- Licence: CC BY 4.0 is still recorded; the condition holds.
- `g2_nw_hn` stays the commercial candidate, and `g2_hn` stays the internal-research reference.

**Other paired results (validation; test in brackets).**
- `g2_s2_hn` against `g2_hn`: F1 −0.054 (−0.108 to +0.010) [test −0.064, −0.094 to −0.032, worse]; false-alarm area +12.2 (−1.0 to +28.7) [test +18.0, +5.7 to +34.9, worse].
- `g2_s2_hn` against the G1 random forest: F1 +0.183 (+0.120 to +0.262) [test +0.186].
- The fine-tune hurt this time: `g2_s2_hn` against `g2_s2`: F1 −0.067 (−0.132 to +0.015), false-alarm area +12.1 (−4.0 to +33.1). With `g2_hn` the same step cut false-alarm area by 31%; here the checkpoint rule kept epoch 1, so the mined negatives had little chance to act.
- `g2_s2` against `g2_nw` (both before hard negatives): F1 +0.083 (+0.025 to +0.134), false-alarm area −17.1 (−34.6 to −3.0); test F1 +0.036 (−0.023 to +0.105). Sentinel-2 pretraining helps the plain run on validation.

**Chosen after seeing the results, so descriptive only.** Because `g2_s2` looked better than `g2_s2_hn`, it was also compared with the two references (`docs/evidence/g2_s2/posthoc_*`):
- against `g2_nw_hn`: validation F1 +0.067 (+0.006 to +0.116), test −0.001 (−0.049 to +0.039);
- against `g2_hn`: validation +0.013 (−0.048 to +0.085), test −0.059 (−0.090 to −0.023), worse.
The validation lead does not carry over to test, and validation already chose each run's checkpoint and threshold, so it flatters every run. This is not grounds to adopt `g2_s2`; a new rule would have to be declared first and checked on data not yet used, such as the Nigerian sites.

**Calibration.** `g2_s2_hn` is not calibrated: on test, ECE on pixels predicted ≥ 0.1 is 0.405 raw and 0.161 after Platt (0.058–0.281). Keep "model score".

**Registry.** `g2_s2` and `g2_s2_hn` are registered as experimental. Their licence lineage lists the SSL4EO-S12 weights as CC BY 4.0 with attribution and hash; the Global ML-ready share-alike question still applies to them, as to every run.

**What this means.**
1. Open Sentinel-2 pretraining under CC BY 4.0 gives a clean licence route, but no model trained this way has beaten `g2_nw_hn` on the declared test. The gap to `g2_hn` (ImageNet weights) remains on test.
2. The encoder differs in size (ResNet-18 against ResNet-34), so this does not show whether pretraining or size is the limit.
3. The hard-negative fine-tune needs a checkpoint rule that does not stop at epoch 1, or a lower learning rate, before it is tried again with these weights. That would be a new run with its own declared rule.
