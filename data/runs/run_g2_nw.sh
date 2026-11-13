#!/bin/bash
# G2 without pretrained encoder weights (licence question removed), then hard-negative fine-tune. Resumable.
cd /home/user/Ajgems && source .venv/bin/activate
COMMON="--sites data/g1/sites.json --split data/splits/gmlr_split.gpkg --prepared-root data/prepared/global_ml_ready --labels-root data/raw/global_ml_ready --worldcover-root data/worldcover --device cpu --workers 2 --encoder-weights none"
if [ ! -f data/runs/g2_nw/metrics.json ]; then
  R=""; [ -f data/runs/g2_nw/last.pt ] && R="--resume"
  minefoot g2 $COMMON --out data/runs/g2_nw --epochs 60 $R || { echo "STAGE1 FAILED"; exit 1; }
fi
echo "STAGE1 DONE"
[ -f data/negatives/g2_nw_mined/summary.json ] || minefoot mine-negatives data/runs/g2_nw --sites data/g1/sites.json \
  --prepared-root data/prepared/global_ml_ready --labels-root data/raw/global_ml_ready --out data/negatives/g2_nw_mined \
  --worldcover-root data/worldcover || { echo "MINE FAILED"; exit 1; }
echo "MINE DONE"
if [ ! -f data/runs/g2_nw_hn/metrics.json ]; then
  R=""; [ -f data/runs/g2_nw_hn/last.pt ] && R="--resume"
  minefoot g2 $COMMON --out data/runs/g2_nw_hn --negatives-root data/negatives/g2_nw_mined \
    --init-from data/runs/g2_nw/unet_best.pt --epochs 12 --lr 0.0003 $R || { echo "STAGE2 FAILED"; exit 1; }
fi
echo "ALL DONE"
