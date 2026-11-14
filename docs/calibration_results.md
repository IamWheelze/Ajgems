# Score calibration, `g2_hn` (FRD 8.5, part of gate G5)

Run 2026-11-14 with `minefoot calibrate data/runs/g2_hn --prepared-root data/prepared/global_ml_ready --labels-root data/raw/global_ml_ready`.
Evidence: `docs/evidence/g2_hn/calibration.json` (reliability tables, maps and intervals for every stratum below).

**Verdict: `g2_hn`'s scores are not calibrated, and a calibration map fitted on validation does not fix them on test. Keep calling them "model score".**

## Method

- The map is fitted on the 60 global validation sites and measured once on the 60 locked global test sites. Neither set was used for training. The threshold was chosen on validation only.
- The applied method was fixed before looking at test: Platt scaling, which fits two parameters on the logit of the score. Pixels within a site are strongly correlated, so the effective sample is about 60 sites, not millions of pixels. Isotonic regression is reported only as a check.
- Both maps are monotone. Pixel order, and so every threshold-based metric in `docs/g2_results.md`, is unchanged.
- ECE uses 10 equal-width bins, weighted by pixel count. About 90% of usable pixels score below 0.1, so plain ECE is dominated by easy background. **ECE ≥ 0.1** is the same measure restricted to pixels predicted at 0.1 or above, where candidates come from. Use it to judge calibration.
- Intervals are 95%, from resampling sites (1,000 resamples). The isotonic interval on validation does not contain its own point value of 0: the map was fitted to exactly that set.
- The pooled pixel counts are checked against the stored run, so these numbers describe the scores that were evaluated.

## Results

Platt fit: p = sigmoid(0.658 · logit(score) − 0.823). At the validation threshold (raw score 0.41), the Platt value is 0.26 and the isotonic value 0.28.

| Set | Stratum | Sites | ECE ≥ 0.1, raw | ECE ≥ 0.1, Platt | ECE ≥ 0.1, isotonic | Brier raw → Platt |
| --- | --- | --- | --- | --- | --- | --- |
| Validation | all | 60 | 0.161 (0.091–0.245) | 0.035 (0.020–0.106) | 0.000 (fitted here) | 0.032 → 0.028 |
| Test | all | 60 | 0.290 (0.129–0.403) | 0.149 (0.059–0.245) | 0.101 (0.032–0.193) | 0.051 → 0.041 |
| Test | Industrial | 40 | 0.368 (0.179–0.494) | 0.203 (0.047–0.324) | 0.164 | 0.058 → 0.042 |
| Test | Artisanal | 20 | 0.099 (0.083–0.184) | 0.209 (0.094–0.338) | 0.196 | 0.037 → 0.039 |
| Test | Africa | 22 | 0.390 (0.159–0.500) | 0.225 (0.041–0.331) | 0.188 | 0.057 → 0.039 |
| Test | Americas | 12 | 0.152 (0.109–0.267) | 0.130 (0.075–0.354) | 0.140 | 0.037 → 0.036 |
| Test | Asia and Oceania | 13 | 0.126 (0.090–0.178) | 0.051 (0.029–0.151) | 0.046 | 0.034 → 0.032 |
| Test | Europe and North Asia | 13 | 0.371 (0.080–0.585) | 0.249 (0.154–0.416) | 0.199 | 0.071 → 0.058 |

Test reliability after Platt (all sites; share of pixels labelled mining in each bin):

| Platt value | 0.1–0.2 | 0.2–0.3 | 0.3–0.4 | 0.4–0.5 | 0.5–0.6 | 0.6–0.7 | 0.7–0.8 | 0.8–0.9 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Labelled share | 0.21 | 0.27 | 0.31 | 0.35 | 0.38 | 0.41 | 0.45 | 0.53 |

The raw scores are overconfident on both sets. Raw scores of 0.9 to 1.0 cover 73% labelled pixels on validation and 48% on test.

## What this means

1. **Platt is close to calibrated on validation, but not on test.** On test, a Platt value of 0.75 covers about 45% labelled mining. The test sites are harder than validation (test F1 0.48), so the map learned on validation over-promises on test. Calibration did not carry over even between two global sets drawn the same way. It cannot be assumed to carry over to Nigeria.
2. **One map for all mine scales hurts artisanal sites.** Raw artisanal scores were already the least miscalibrated (ECE ≥ 0.1 of 0.099). Platt, pulled mostly by industrial pixels, makes artisanal worse (0.209). A per-scale map would need more validation sites than the 20 artisanal ones here.
3. **Labels are part of the gap.** Artisanal labels are loose (`docs/m1_global_ml_ready_check.md`), so "labelled share" understates real disturbance in some bins. Calibration is measured against labels, not against the ground.
4. **No calibrated value is shown or exported.** The web app and exports keep showing the model score and its band. Nothing in the job pipeline applies these maps.

## Next

- Recheck on the locked Nigerian test set at G3, using the same command on the G3 run. Do not fit there: the Nigerian test set must not be used for threshold or calibration choices (ML-07). A Nigerian calibration needs a separate Nigerian calibration set, which belongs to G4/G5.
- Repeat for `g2_nw_hn` once its retrain finishes.
