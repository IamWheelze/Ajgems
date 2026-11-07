# Running gate G2 on your own machine

G2 trains a U-Net (ResNet-34 encoder) on the same 60/60/60 Global ML-ready sites as G1, scores it with the same evaluation, and compares it with the G1 random forest. Internal research only.

The cloud session could not reach your machine, so it built and tested the code there (CPU, small runs) and left these steps. Every step checks its own result: the dataset checksum, the split against the committed v1 split, the test set against its lock, and the comparison refuses runs scored on different data.

## What you need

- An NVIDIA GPU with 8 GB or more (Apple Silicon also works, more slowly; CPU is too slow for 40 epochs).
- About 20 GB free disk.
- Internet access to Zenodo, Planetary Computer and its storage (`planetarycomputer.microsoft.com`, `sentinel2l2a01.blob.core.windows.net`, `ai4edataeuwest.blob.core.windows.net`) and the Hugging Face Hub (for the pretrained encoder).
- Python 3.10 or later and Git.

## Steps

From the repository root, on branch `claude/new-session-c7swec`.

1. **Environment.** Install PyTorch and torchvision for your GPU first, together and from the same index, using the command [pytorch.org](https://pytorch.org/get-started/locally/) gives for your CUDA version. Then install the rest. A torchvision from a different index fails with `operator torchvision::nms does not exist`.

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
   pip install -e ".[g2,dev]"
   python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
   ```

2. **Data and split.** The split must come out identical to the committed v1 split; the command stops if it does not.

   ```bash
   minefoot download-global-ml-ready
   minefoot ingest global-ml-ready data/external/global_ml_ready/mining_area_data.gpkg
   minefoot split data/datasets/global_ml_ready/sites.gpkg --out data/splits/gmlr_split.gpkg --expect docs/evidence/split_global_ml_ready_v1.csv
   minefoot check-split data/splits/gmlr_split.gpkg --lock docs/evidence/test_lock_global_ml_ready_v1.json
   ```

3. **Fetch and prepare the 180 sites.** This takes 1 to 2 hours, mostly downloading, and can be stopped and restarted.

   ```bash
   minefoot get-sites docs/evidence/g1_v1/sites.json
   ```

4. **Train and score G2.**

   ```bash
   minefoot g2 --sites docs/evidence/g1_v1/sites.json --split data/splits/gmlr_split.gpkg --prepared-root data/prepared/global_ml_ready --labels-root data/raw/global_ml_ready --worldcover-root data/worldcover --out data/runs/g2_v1
   ```

   The defaults are 40 epochs, 32 chips of 256 × 256 per training site per epoch, batch 16, AdamW at 1e-3 with cosine decay, and mixed precision on CUDA. If memory runs out, add `--batch 8`; on Windows, if data loading fails, add `--workers 0`. The run writes `config.json`, `history.csv`, `normalisation.json`, `unet_best.pt`, `metrics.json`, score maps, test error maps and `false_alerts_by_type.json`.

5. **Compare with G1, paired, on validation first.** The G1 numbers come from the committed `docs/evidence/g1_v1/metrics.json`. If your prepared data differed from the data G1 was scored on, `compare` refuses and names the sites.

   ```bash
   minefoot compare --a docs/evidence/g1_v1/metrics.json:random_forest --b data/runs/g2_v1/metrics.json:unet --split val --out data/runs/g2_v1/paired_vs_g1_val.json
   minefoot compare --a docs/evidence/g1_v1/metrics.json:random_forest --b data/runs/g2_v1/metrics.json:unet --split test --out data/runs/g2_v1/paired_vs_g1_test.json
   minefoot error-gallery data/runs/g2_v1 --model unet --prepared-root data/prepared/global_ml_ready --out results/g2_v1_unet_errors.html
   ```

6. **Bring the results back.** Copy the small files into the repository, then commit and push:

   ```bash
   mkdir -p docs/evidence/g2_v1
   cp data/runs/g2_v1/{config.json,history.csv,normalisation.json,metrics.json,false_alerts_by_type.json,paired_vs_g1_val.json,paired_vs_g1_test.json} docs/evidence/g2_v1/
   git add docs/evidence/g2_v1 results/g2_v1_unet_errors.html
   git commit -m "Add G2 v1 results"
   git push
   ```

   On Windows without Git Bash, copy those seven files into `docs\g2_v1` by hand instead of using `cp`. A later Claude session can then read them and write up the gate decision. Keep `unet_best.pt` (about 100 MB) on your machine; it is not committed.

## How G2 is judged

From FRD 8.3: G2 passes if it **beats the G1 random forest at site level by a margin beyond its confidence interval**. Read that from `compare`: the 95% interval of the difference must lie wholly above zero on pixel F1, and footprint coverage must be reported alongside. Decide on validation; the test comparison is reported, not tuned on. Also read false candidates per 1,000 km² and the per-type false alerts: G1's main failure was bare fields and dry stream beds.

Rules that still apply: the split v1 test set is locked; no hyperparameter search beyond 2 or 3 experiments at this gate; scores are "model scores". Nothing here measures Nigerian performance.

The pretrained encoder weights are ImageNet-derived, and their licence is not yet recorded in `docs/licence_register.csv`. Fine for internal research; resolve it before any release.
