"""Gate G2 (FRD 8.3): U-Net with a ResNet encoder, on the same sites and evaluation as G1.

Internal research only. Train sites fit the network and its normalisation (ML-06),
fixed validation chips pick the checkpoint, the full validation tiles pick the
score threshold, and test tiles are scored once. Inputs are the prepared
reflectance from `minefoot.preprocess` (ML-02). Masked pixels (NaN) are excluded
from the loss and from every metric. Scores are model scores, not probabilities.

G2 passes only if it beats the G1 random forest at site level by more than the
confidence interval: compare with `minefoot compare` (paired site bootstrap).
Needs PyTorch and segmentation-models-pytorch (`pip install -e ".[g2]"`).
"""
from __future__ import annotations
import csv
import json
import time
from pathlib import Path

import numpy as np

from .baseline import (base_config, check_inputs, choose_thresholds, evaluate_scores, score_held_out,
                       add_type_breakdown, load_site)
from .preprocess import iter_chips
from .schema import BANDS

G2_DEFAULTS = {"encoder": "resnet34", "encoder_weights": "imagenet", "chip": 256, "batch": 16, "epochs": 40,
               "chips_per_site": 32, "positive_fraction": 0.5, "lr": 1e-3, "weight_decay": 1e-4,
               "val_chips_per_site": 8, "infer_overlap": None, "workers": 4, "amp": True}


def pick_device(prefer: str | None = None):
    import torch
    if prefer:
        return torch.device(prefer)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


# ---------- data ----------

def cache_site(prepared_dir, labels_path, cache_dir) -> Path:
    """Rasterised label (uint8) on the site grid, cached once per site."""
    import rasterio
    from .labels import rasterize_to, read_vectors
    out = Path(cache_dir) / f"{Path(prepared_dir).name}_label.tif"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        lab = rasterize_to(Path(prepared_dir) / "reflectance.tif", read_vectors(labels_path)).astype("uint8")
        with rasterio.open(Path(prepared_dir) / "reflectance.tif") as s:
            p = {**s.profile, "count": 1, "dtype": "uint8", "nodata": None, "compress": "deflate"}
        with rasterio.open(out, "w", **p) as d:
            d.write(lab, 1)
    return out


def fit_normalisation(train_paths, n_per_site=20000, seed=0) -> dict:
    """Per-band mean and standard deviation from usable training pixels only (ML-06)."""
    import rasterio
    rng = np.random.default_rng(seed)
    samples = []
    for _, prep, _ in train_paths:
        with rasterio.open(Path(prep) / "reflectance.tif") as s:
            a = s.read().reshape(len(BANDS), -1)
        ok = np.flatnonzero(np.isfinite(a).all(axis=0))
        if ok.size:
            samples.append(a[:, rng.choice(ok, min(n_per_site, ok.size), replace=False)])
    x = np.concatenate(samples, axis=1)
    return {"bands": list(BANDS), "mean": x.mean(axis=1).tolist(), "std": (x.std(axis=1) + 1e-6).tolist(),
            "source": f"{len(samples)} training sites, up to {n_per_site} usable pixels each"}


def normalise(refl: np.ndarray, norm: dict) -> tuple[np.ndarray, np.ndarray]:
    """(bands, H, W) reflectance to normalised float32 with NaN set to 0, plus the usable mask."""
    m = np.asarray(norm["mean"], "float32")[:, None, None]
    s = np.asarray(norm["std"], "float32")[:, None, None]
    valid = np.isfinite(refl).all(axis=0)
    x = np.nan_to_num((refl.astype("float32") - m) / s, nan=0.0)
    return x, valid


class ChipDataset:
    """Random chips per epoch: a share centred on mining pixels, the rest anywhere. Read on demand."""

    def __init__(self, sites, label_paths, norm, chip, per_site, positive_fraction, seed, augment=True, fixed=False):
        self.sites, self.labels, self.norm = sites, label_paths, norm
        self.chip, self.per_site, self.pos = chip, per_site, positive_fraction
        self.seed, self.augment, self.fixed, self.epoch = seed, augment, fixed, 0
        self._pos_index = {}

    def __len__(self):
        return len(self.sites) * self.per_site

    def _positives(self, i):
        if i not in self._pos_index:
            import rasterio
            with rasterio.open(self.labels[i]) as s:
                lab = s.read(1, out_shape=(s.height // 8, s.width // 8))   # coarse index is enough to centre chips
                self._pos_index[i] = (np.argwhere(lab > 0) * 8, (s.height, s.width))
        return self._pos_index[i]

    def __getitem__(self, k):
        import rasterio
        from rasterio.windows import Window
        i = k // self.per_site
        rng = np.random.default_rng([self.seed, 0 if self.fixed else self.epoch, k])
        pos, (h, w) = self._positives(i)
        c = self.chip
        if pos.size and rng.random() < self.pos:
            cy, cx = pos[rng.integers(len(pos))] + rng.integers(-c // 4, c // 4 + 1, 2)
            r0, c0 = int(np.clip(cy - c // 2, 0, h - c)), int(np.clip(cx - c // 2, 0, w - c))
        else:
            r0, c0 = int(rng.integers(0, h - c + 1)), int(rng.integers(0, w - c + 1))
        win = Window(c0, r0, c, c)
        with rasterio.open(Path(self.sites[i]) / "reflectance.tif") as s:
            refl = s.read(window=win)
        with rasterio.open(self.labels[i]) as s:
            lab = s.read(1, window=win).astype("float32")
        x, valid = normalise(refl, self.norm)
        if self.augment:
            kk = int(rng.integers(4))
            x, lab, valid = np.rot90(x, kk, (1, 2)), np.rot90(lab, kk), np.rot90(valid, kk)
            if rng.random() < 0.5:
                x, lab, valid = x[:, :, ::-1], lab[:, ::-1], valid[:, ::-1]
        return (np.ascontiguousarray(x), np.ascontiguousarray(lab[None]),
                np.ascontiguousarray(valid[None].astype("float32")))


# ---------- model ----------

def build_model(encoder="resnet34", encoder_weights="imagenet"):
    import segmentation_models_pytorch as smp
    w = None if encoder_weights in (None, "none", "None") else encoder_weights
    return smp.Unet(encoder_name=encoder, encoder_weights=w, in_channels=len(BANDS), classes=1)


def masked_loss(logits, target, valid):
    """Binary cross-entropy plus soft Dice, over usable pixels only."""
    import torch
    import torch.nn.functional as F
    n = valid.sum().clamp(min=1)
    bce = (F.binary_cross_entropy_with_logits(logits, target, reduction="none") * valid).sum() / n
    p = torch.sigmoid(logits) * valid
    t = target * valid
    dice = 1 - (2 * (p * t).sum() + 1) / (p.sum() + t.sum() + 1)
    return bce + dice


def predict_tile(model, refl, norm, device, chip=256, overlap=64, batch=8) -> np.ndarray:
    """Whole-tile score in [0, 1] from overlapping chips, blended with a tapered weight (FR-060)."""
    import torch
    x, valid = normalise(refl, norm)
    h, w = valid.shape
    if h < chip or w < chip:  # pad small tiles
        ph, pw = max(chip - h, 0), max(chip - w, 0)
        x = np.pad(x, ((0, 0), (0, ph), (0, pw)))
    H, W = x.shape[1:]
    acc = np.zeros((H, W), "float32"); wsum = np.zeros((H, W), "float32")
    ramp = np.minimum(np.arange(chip) + 1, np.arange(chip)[::-1] + 1).astype("float32")
    weight = np.minimum.outer(ramp, ramp); weight /= weight.max()
    coords = list(iter_chips(H, W, chip, overlap))
    model.eval()
    with torch.no_grad():
        for i in range(0, len(coords), batch):
            part = coords[i:i + batch]
            xb = torch.from_numpy(np.stack([x[:, r:r + chip, c:c + chip] for r, c in part])).to(device)
            pb = torch.sigmoid(model(xb)).float().cpu().numpy()[:, 0]
            for (r, c), pr in zip(part, pb):
                acc[r:r + chip, c:c + chip] += pr * weight
                wsum[r:r + chip, c:c + chip] += weight
    out = (acc / np.maximum(wsum, 1e-6))[:h, :w]
    return np.where(valid, out, 0).astype("float32")


# ---------- run ----------

def run_g2(sites_by_split: dict, split_gpkg, prepared_root, labels_root, out_dir, seed=0, log=print,
           worldcover_root=None, device=None, **overrides) -> dict:
    """Train, choose and score a U-Net on the same sites and evaluation as G1. Writes a lineage record."""
    import torch
    cfg = {**G2_DEFAULTS, **{k: v for k, v in overrides.items() if v is not None}}
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    meta, region, paths = check_inputs(sites_by_split, split_gpkg, prepared_root, labels_root)
    torch.manual_seed(seed); np.random.seed(seed)
    dev = pick_device(device)
    config = base_config("G2", sites_by_split, split_gpkg, seed)
    config.update({"model": "U-Net (segmentation_models_pytorch)", "params": cfg, "device": str(dev),
                   "loss": "masked binary cross-entropy + soft Dice; masked pixels excluded",
                   "checkpoint_rule": "lowest loss on fixed validation chips",
                   "encoder_weights_note": "ImageNet-pretrained encoder weights: licence of the weights not yet "
                                           "recorded in docs/licence_register.csv; internal research only"})
    import segmentation_models_pytorch as smp
    config["environment"].update(torch=torch.__version__, smp=smp.__version__,
                                 gpu=torch.cuda.get_device_name(0) if dev.type == "cuda" else None)
    (out / "config.json").write_text(json.dumps(config, indent=2))

    t0 = time.time()
    cache = out / "cache"
    tr, va = paths(sites_by_split["train"]), paths(sites_by_split["val"])
    tr_labels = [cache_site(p, l, cache) for _, p, l in tr]
    va_labels = [cache_site(p, l, cache) for _, p, l in va]
    norm = fit_normalisation(tr, seed=seed)
    (out / "normalisation.json").write_text(json.dumps(norm, indent=2))
    log(f"normalisation fitted on {norm['source']}")

    train_ds = ChipDataset([p for _, p, _ in tr], tr_labels, norm, cfg["chip"], cfg["chips_per_site"],
                           cfg["positive_fraction"], seed)
    val_ds = ChipDataset([p for _, p, _ in va], va_labels, norm, cfg["chip"], cfg["val_chips_per_site"],
                         cfg["positive_fraction"], seed + 1, augment=False, fixed=True)
    loader_kw = dict(batch_size=cfg["batch"], num_workers=cfg["workers"], pin_memory=dev.type == "cuda",
                     persistent_workers=False)
    from torch.utils.data import DataLoader
    model = build_model(cfg["encoder"], cfg["encoder_weights"]).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=cfg["epochs"])
    use_amp = bool(cfg["amp"]) and dev.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    best, history = float("inf"), []
    for epoch in range(cfg["epochs"]):
        train_ds.epoch = epoch
        model.train(); tl, tn = 0.0, 0
        g = torch.Generator(); g.manual_seed(seed * 1000 + epoch)
        for xb, yb, vb in DataLoader(train_ds, shuffle=True, generator=g, **loader_kw):
            xb, yb, vb = xb.to(dev), yb.to(dev), vb.to(dev)
            opt.zero_grad(set_to_none=True)
            with torch.autocast(device_type=dev.type, enabled=use_amp):
                loss = masked_loss(model(xb), yb, vb)
            scaler.scale(loss).backward(); scaler.step(opt); scaler.update()
            tl += loss.item() * len(xb); tn += len(xb)
        sched.step()
        model.eval(); vl, vn = 0.0, 0
        with torch.no_grad():
            for xb, yb, vb in DataLoader(val_ds, shuffle=False, **loader_kw):
                xb, yb, vb = xb.to(dev), yb.to(dev), vb.to(dev)
                vl += float(masked_loss(model(xb), yb, vb)) * len(xb); vn += len(xb)
        row = {"epoch": epoch + 1, "train_loss": tl / max(tn, 1), "val_chip_loss": vl / max(vn, 1),
               "lr": sched.get_last_lr()[0], "elapsed_s": round(time.time() - t0, 1)}
        history.append(row)
        if row["val_chip_loss"] < best:
            best = row["val_chip_loss"]; row["checkpoint"] = True
            torch.save(model.state_dict(), out / "unet_best.pt")
        log(f"epoch {epoch + 1}/{cfg['epochs']} train {row['train_loss']:.4f} val-chips {row['val_chip_loss']:.4f}"
            f"{' *' if row.get('checkpoint') else ''} ({row['elapsed_s']:.0f}s)")
        with open(out / "history.csv", "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=["epoch", "train_loss", "val_chip_loss", "lr", "elapsed_s", "checkpoint"])
            wr.writeheader(); wr.writerows(history)

    model.load_state_dict(torch.load(out / "unet_best.pt", map_location=dev))
    best_epoch = min(history, key=lambda r: r["val_chip_loss"])["epoch"]
    overlap = cfg["infer_overlap"] or cfg["chip"] // 4
    scorer = lambda feats, valid: predict_tile(model, feats[:len(BANDS)], norm, dev, cfg["chip"], overlap)
    state = score_held_out(out, {"unet": scorer}, sites_by_split, prepared_root, labels_root, log, t0)
    chosen = choose_thresholds(state, sites_by_split)
    log(f"validation F1: unet {chosen['unet'][1]:.3f} at level {chosen['unet'][0]}")
    results = evaluate_scores(out, ("unet",), chosen, state, sites_by_split, meta, region, seed)
    metrics = {"models": results, "validation_choice": {"unet": {"level": chosen["unet"][0], "f1": chosen["unet"][1]}},
               "best_epoch": best_epoch, "epochs": cfg["epochs"], "runtime_s": round(time.time() - t0, 1)}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    if worldcover_root:
        add_type_breakdown(out, metrics, results, prepared_root, labels_root, worldcover_root)
    return metrics
