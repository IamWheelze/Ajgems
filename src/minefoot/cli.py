"""Command line: doctor, g2, search, fetch, prepare, view, inspect-dataset, ingest, fetch-sites, label-audit, mask-report, overlaps, split, lock-test, check-split, g1, compare, false-alerts, negatives-template, negatives-check, error-gallery, rescore, curves, ingest-pool, conditions-eval, mine-negatives, rasterize-negatives, labelling-kit, labels-check, labels-progress, lock-labels, verify-lock, g3, demo."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path


def _bbox(s):
    v = [float(x) for x in s.split(",")]
    if len(v) != 4 or v[0] >= v[2] or v[1] >= v[3]:
        raise argparse.ArgumentTypeError("use west,south,east,north")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(prog="minefoot", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check catalogue access and band names on this machine")
    from .catalog import PROVIDERS
    prov_help = "use only this catalogue (default: try each in order)"
    a = sub.add_parser("search"); a.add_argument("--bbox", type=_bbox, required=True); a.add_argument("--start", required=True)
    a.add_argument("--end", required=True); a.add_argument("--max-cloud", type=float, default=40)
    a.add_argument("--provider", choices=[p for p in PROVIDERS if PROVIDERS[p]["url"]], help=prov_help)
    a = sub.add_parser("fetch"); a.add_argument("--bbox", type=_bbox, required=True); a.add_argument("--start", required=True)
    a.add_argument("--end", required=True); a.add_argument("--max-cloud", type=float, default=40)
    a.add_argument("--provider", choices=[p for p in PROVIDERS if PROVIDERS[p]["url"]], help=prov_help)
    a.add_argument("--scenes", type=int, default=1); a.add_argument("--scene-id"); a.add_argument("--out", default="data/raw")
    a = sub.add_parser("prepare"); a.add_argument("raw"); a.add_argument("--out", default="data/prepared")
    a = sub.add_parser("view", help="inspection package: HTML gallery, COGs, GeoPackage and a QGIS project (FR-133)")
    a.add_argument("prepared", nargs="+"); a.add_argument("--labels", help="one polygon file used for every scene")
    a.add_argument("--labels-root", help="folder holding <scene folder>/labels.geojson, as written by fetch-sites")
    a.add_argument("--title", default="Scene inspection"); a.add_argument("--out", default="data/view")
    a = sub.add_parser("inspect-dataset"); a.add_argument("path")
    a = sub.add_parser("ingest", help="convert an external dataset into the common site schema")
    a.add_argument("dataset", choices=["global-ml-ready"]); a.add_argument("path")
    a.add_argument("--label-layer", default="preferred_polygons"); a.add_argument("--out", default="data/datasets/global_ml_ready")
    a = sub.add_parser("fetch-sites", help="fetch each site's exact scene and labels")
    a.add_argument("sites", help="sites.gpkg written by ingest"); a.add_argument("--n", type=int, default=50)
    a.add_argument("--site-id", action="append", help="fetch only these sites (repeatable)")
    a.add_argument("--seed", type=int, default=0); a.add_argument("--provider", choices=[p for p in PROVIDERS if PROVIDERS[p]["url"]])
    a.add_argument("--out", default="data/raw/global_ml_ready")
    a = sub.add_parser("label-audit", help="how much labelled ground the quality mask removes, per scene")
    a.add_argument("prepared", nargs="+"); a.add_argument("--raw-root", required=True,
                   help="folder holding <scene>/labels.geojson"); a.add_argument("--csv")
    a = sub.add_parser("mask-report", help="share of each polygon (candidate, label, area) the quality mask removed")
    a.add_argument("prepared"); a.add_argument("--polygons", required=True)
    a.add_argument("--out", required=True, help="output .gpkg; a .csv is written next to it")
    a = sub.add_parser("overlaps", help="duplicate scenes and overlapping sites or labels across datasets (FR-072)")
    a.add_argument("sites", nargs="+", help="one or more sites.gpkg"); a.add_argument("--out", required=True, help=".csv")
    a = sub.add_parser("split", help="assign train/val/test by site and geographic block with a buffer (FR-077)")
    a.add_argument("sites", nargs="+"); a.add_argument("--out", required=True, help=".gpkg")
    a.add_argument("--block-km", type=float, default=50.0); a.add_argument("--buffer-km", type=float, default=10.0)
    a.add_argument("--fractions", default="0.7,0.15,0.15"); a.add_argument("--seed", type=int, default=0)
    a.add_argument("--lock", help="existing test lock to respect")
    a.add_argument("--stratify", default="mine_scale", help="column to balance across splits; 'none' to switch off")
    a.add_argument("--expect", help="committed split CSV this split must reproduce exactly")
    a = sub.add_parser("download-global-ml-ready", help="download and checksum the Global ML-ready zip from Zenodo")
    a.add_argument("--out", default="data/external/global_ml_ready")
    a = sub.add_parser("get-sites", help="fetch and prepare every site in a {split: [site_id]} JSON, resuming")
    a.add_argument("sites_json"); a.add_argument("--sites-gpkg", default="data/datasets/global_ml_ready/sites.gpkg")
    a.add_argument("--raw-root", default="data/raw/global_ml_ready")
    a.add_argument("--prepared-root", default="data/prepared/global_ml_ready")
    a.add_argument("--keep-raw", action="store_true")
    a = sub.add_parser("lock-test", help="freeze the test sites of a split; never overwrites")
    a.add_argument("split"); a.add_argument("--out", required=True); a.add_argument("--note", default="")
    a = sub.add_parser("check-split", help="leakage test for a split file (ML-03)")
    a.add_argument("split"); a.add_argument("--buffer-km", type=float, default=10.0)
    a.add_argument("--split-column", default="split"); a.add_argument("--lock"); a.add_argument("--overlaps")
    a = sub.add_parser("g1", help="gate G1 baselines: index threshold and random forest (internal research)")
    a.add_argument("--sites", required=True, help="JSON {train: [...], val: [...], test: [...]} of site IDs")
    a.add_argument("--split", required=True, help="split .gpkg the site lists must agree with")
    a.add_argument("--prepared-root", required=True); a.add_argument("--labels-root", required=True)
    a.add_argument("--out", required=True); a.add_argument("--seed", type=int, default=0)
    a.add_argument("--worldcover-root", help="cache folder for WorldCover; enables per-type false-alert rates")
    a.add_argument("--hard-negatives-per-type", type=int, default=0,
                   help="add this many WorldCover-typed negative pixels per type per training site (needs --worldcover-root)")
    a.add_argument("--hard-negative-buffer-px", type=int, default=50)
    a.add_argument("--hard-negative-types", default="farmland,settlement,riverbank,bare_ground",
                   help="comma-separated proxy types to add")
    a = sub.add_parser("g2", help="gate G2: U-Net on the same sites and evaluation as G1 (needs .[g2])")
    a.add_argument("--sites", required=True); a.add_argument("--split", required=True)
    a.add_argument("--prepared-root", required=True); a.add_argument("--labels-root", required=True)
    a.add_argument("--out", required=True); a.add_argument("--seed", type=int, default=0)
    a.add_argument("--worldcover-root"); a.add_argument("--device", help="cuda, mps or cpu (default: best available)")
    for k, t in (("epochs", int), ("batch", int), ("chips-per-site", int), ("chip", int), ("workers", int),
                 ("lr", float), ("encoder", str), ("encoder-weights", str)):
        a.add_argument(f"--{k}", type=t)
    a.add_argument("--no-amp", action="store_true")
    a.add_argument("--resume", action="store_true", help="continue an interrupted run in --out from its last epoch")
    a.add_argument("--negatives-root", help="folder of <site>_neg.tif hard-negative masks (mine-negatives, rasterize-negatives)")
    a.add_argument("--negative-fraction", type=float, help="share of training chips centred on hard negatives (default 0.25)")
    a.add_argument("--init-from", help="checkpoint to fine-tune from (its run's normalisation is reused)")
    a = sub.add_parser("mine-negatives", help="hard-negative mining on TRAINING sites with a finished G2 run")
    a.add_argument("run"); a.add_argument("--sites", required=True, help="sites JSON; only its train list is used")
    a.add_argument("--prepared-root", required=True); a.add_argument("--labels-root", required=True)
    a.add_argument("--out", required=True); a.add_argument("--worldcover-root")
    a.add_argument("--buffer-px", type=int, default=50)
    a = sub.add_parser("rasterize-negatives", help="digitised hard-negative polygons to per-site masks")
    a.add_argument("gpkg"); a.add_argument("--sites", required=True, help="sites JSON; only its train list is used")
    a.add_argument("--prepared-root", required=True); a.add_argument("--out", required=True)
    a = sub.add_parser("false-alerts", help="per-type false-alert rates for a stored run (ML-08, WorldCover proxy types)")
    a.add_argument("run"); a.add_argument("--model", required=True); a.add_argument("--split", default="val", choices=["val", "test"])
    a.add_argument("--prepared-root", required=True); a.add_argument("--labels-root", required=True)
    a.add_argument("--worldcover-root", required=True); a.add_argument("--out")
    a = sub.add_parser("negatives-template", help="empty typed hard-negative GeoPackage and QGIS form style (FR-074)")
    a.add_argument("out")
    a = sub.add_parser("negatives-check", help="counts per type and problems in a digitised hard-negative layer")
    a.add_argument("path")
    a = sub.add_parser("error-gallery", help="HTML gallery of a run's error maps, worst sites first (FR-129)")
    a.add_argument("run"); a.add_argument("--model", required=True); a.add_argument("--prepared-root", required=True)
    a.add_argument("--split", default="test", choices=["test"]); a.add_argument("--limit", type=int, default=20)
    a.add_argument("--out", required=True)
    a = sub.add_parser("rescore", help="recompute footprint metrics of a stored run with the current definition")
    a.add_argument("run"); a.add_argument("--prepared-root", required=True); a.add_argument("--labels-root", required=True)
    a = sub.add_parser("conditions-eval", help="measure the FR-054 condition flags against labelled references")
    a.add_argument("--prepared-root", required=True); a.add_argument("--worldcover-root", required=True)
    a.add_argument("--sites", required=True, help="sites.json from a run (uses its test and validation sites)"); a.add_argument("--out")
    a.add_argument("--labels-root", help="mining labels, to measure how often each prompt fires on mining ground")
    a = sub.add_parser("ingest-pool", help="accepted annotation-pool labels (web export zip) into the common dataset schema")
    a.add_argument("zip"); a.add_argument("--out", required=True); a.add_argument("--version")
    a.add_argument("--locked-split", help="split GeoPackage whose test sites must not be overlapped")
    a.add_argument("--test-regions", default="configs/nigeria_aois.yaml")
    a = sub.add_parser("curves", help="precision-recall and reliability data for a stored run (curves.json)")
    a.add_argument("run"); a.add_argument("--prepared-root", required=True); a.add_argument("--labels-root", required=True)
    a = sub.add_parser("labelling-kit", help="Nigerian labelling kit: imagery per region and a QGIS project to digitise in")
    a.add_argument("config", help="regions YAML, e.g. configs/nigeria_aois.yaml"); a.add_argument("--out", required=True)
    a = sub.add_parser("labels-check", help="problems and per-site counts in a mining-label layer")
    a.add_argument("path"); a.add_argument("--kit", help="kit.json, to check scene IDs")
    a = sub.add_parser("labels-progress", help="review progress per site, and whether the labels can be locked")
    a.add_argument("path"); a.add_argument("--kit")
    a = sub.add_parser("lock-labels", help="freeze labelled Nigerian sites as the locked test set; never overwrites")
    a.add_argument("path"); a.add_argument("--out", required=True); a.add_argument("--kit"); a.add_argument("--note", default="")
    a = sub.add_parser("verify-lock", help="detect edits to locked Nigerian sites")
    a.add_argument("path"); a.add_argument("--lock", required=True)
    a = sub.add_parser("g3", help="gate G3: test a finished model on the locked Nigerian sites (no training, no tuning)")
    a.add_argument("--run", required=True, help="finished G2 run folder, e.g. data/runs/g2_hn")
    a.add_argument("--kit", required=True, help="labelling kit folder (kit.json, prepared/)")
    a.add_argument("--labels", required=True); a.add_argument("--lock", required=True)
    a.add_argument("--negatives", help="digitised hard_negatives.gpkg, for per-type false-alert rates")
    a.add_argument("--out", required=True); a.add_argument("--window-m", type=float, default=1000.0)
    a.add_argument("--access-log", default="docs/evidence/nigeria_test_access_log.csv")
    a.add_argument("--device")
    a = sub.add_parser("compare", help="paired site bootstrap of model B minus model A on the same sites")
    a.add_argument("--a", required=True, help="metrics.json:model"); a.add_argument("--b", required=True, help="metrics.json:model")
    a.add_argument("--split", default="val", choices=["val", "test"]); a.add_argument("--n-boot", type=int, default=2000)
    a.add_argument("--stratum", help="column=value, e.g. mine_scale=Artisanal"); a.add_argument("--out", help=".json")
    a = sub.add_parser("demo"); a.add_argument("--out", default="data/demo")
    x = ap.parse_args(argv)

    if x.cmd == "doctor":
        from .catalog import PROVIDERS, _client, summarise
        ok = False
        for p, c in PROVIDERS.items():
            if c["url"] is None:   # uploaded products have no catalogue
                continue
            try:
                items = list(_client(p).search(collections=[c["collection"]], bbox=[8.8, 9.7, 8.95, 9.85], max_items=1,
                                               datetime="2026-01-01/2026-02-28").items())
                if not items:
                    print(f"{p}: reachable, no scene found"); continue
                missing = [v for v in c["assets"].values() if v not in items[0].assets]
                from .reflectance import resolve
                r = resolve(items[0].properties, items[0].assets[c["assets"]["B04"]].to_dict())
                print(f"{p}: OK {items[0].id} baseline={r.baseline} "
                      f"offset={r.offset_dn} rule={r.rule} missing_assets={missing}")
                ok = ok or not missing
            except Exception as e:
                print(f"{p}: FAILED {type(e).__name__}: {e}")
        return 0 if ok else 1
    if x.cmd in ("search", "fetch"):
        from .catalog import search, summarise, fetch_scene
        kw = {"providers": (x.provider,)} if x.provider else {}
        prov, items = search(x.bbox, x.start, x.end, x.max_cloud, **kw)
        print(f"catalogue: {prov}, scenes: {len(items)}", file=sys.stderr)
        if x.cmd == "search":
            print(json.dumps([summarise(i) for i in items], indent=2)); return 0
        chosen = [i for i in items if i.id == x.scene_id] if x.scene_id else items[: x.scenes]
        if not chosen:
            print("no matching scene", file=sys.stderr); return 1
        for i in chosen:
            print(fetch_scene(i, prov, x.bbox, x.out))
        return 0
    if x.cmd == "prepare":
        from .preprocess import prepare_scene
        i = prepare_scene(x.raw, Path(x.out) / Path(x.raw).name)
        print(json.dumps(i["coverage"], indent=2)); return 0
    if x.cmd == "view":
        from .viewer import build_gallery
        if x.labels and x.labels_root:
            print("use --labels or --labels-root, not both", file=sys.stderr); return 2
        labs = None
        if x.labels:
            labs = {Path(d).name: x.labels for d in x.prepared}
        elif x.labels_root:
            labs = {Path(d).name: Path(x.labels_root) / Path(d).name / "labels.geojson" for d in x.prepared}
            labs = {k: v for k, v in labs.items() if v.exists()}
        page = build_gallery(x.prepared, x.out, labs, x.title)
        print(page); print(Path(x.out) / "project.qgs"); return 0
    if x.cmd == "inspect-dataset":
        from .labels import inspect_dataset
        print(json.dumps(inspect_dataset(x.path), indent=2)); return 0
    if x.cmd == "ingest":
        from .datasets import load_global_ml_ready, write_ingest
        sites, labels, report = load_global_ml_ready(x.path, x.label_layer)
        path = write_ingest(sites, labels, report, x.out)
        print(json.dumps(report, indent=2)); print(path)
        return 1 if report["schema_problems"] else 0
    if x.cmd == "fetch-sites":
        from .datasets import fetch_sites
        r = fetch_sites(x.sites, x.out, n=x.n, site_ids=x.site_id, seed=x.seed, provider=x.provider)
        print(f"fetched {len(r['fetched'])}, already present {len(r['skipped_existing'])}, failed {len(r['failed'])}")
        return 1 if r["failed"] else 0
    if x.cmd == "label-audit":
        import csv
        from .labels import audit_labels
        rows = [audit_labels(d, Path(x.raw_root) / Path(d).name / "labels.geojson") for d in x.prepared]
        rows = [r for r in rows if r["inside_pixels"] > 0]
        if x.csv and rows:
            with open(x.csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        for r in rows:
            print(f"{r['scene']}: cloud inside {r['inside_cloud']:.1%} vs outside {r['outside_cloud']:.1%}; "
                  f"usable inside {r['inside_usable']:.1%} vs outside {r['outside_usable']:.1%}")
        return 0
    if x.cmd == "mask-report":
        from .labels import polygon_mask_report, summarise_mask_report
        r = polygon_mask_report(x.prepared, x.polygons)
        out = Path(x.out); out.parent.mkdir(parents=True, exist_ok=True)
        r.to_file(out, driver="GPKG")
        r.drop(columns="geometry").assign(geometry_wkt=r.geometry.to_wkt()).to_csv(out.with_suffix(".csv"), index=False)
        print(json.dumps(summarise_mask_report(r), indent=2)); print(out)
        return 0
    if x.cmd == "overlaps":
        from .splits import overlap_report, read_sites
        sites, labels = read_sites(x.sites)
        r = overlap_report(sites, labels)
        Path(x.out).parent.mkdir(parents=True, exist_ok=True); r.to_csv(x.out, index=False)
        print(json.dumps({"sites": len(sites), "pairs": len(r),
                          "by_kind": {k: int(v) for k, v in r["kind"].value_counts().items()},
                          "cross_dataset_pairs": int(r["cross_dataset"].sum())}, indent=2))
        return 0
    if x.cmd == "split":
        from .splits import assign_splits, overlap_report, read_lock, read_sites, check_split
        sites, labels = read_sites(x.sites)
        ov = overlap_report(sites, labels)
        lock = read_lock(x.lock) if x.lock else None
        fr = tuple(float(v) for v in x.fractions.split(","))
        strat = None if x.stratify.lower() == "none" else x.stratify
        s, summary = assign_splits(sites, ov, fr, x.block_km, x.buffer_km, x.seed, lock, strat)
        summary["check"] = check_split(s, x.buffer_km, ov, lock)["ok"]
        if x.expect:
            from .splits import compare_to_expected
            diffs = compare_to_expected(s, x.expect)
            summary["matches_expected"] = not diffs
            if diffs:
                print(json.dumps(summary, indent=2))
                print(f"split differs from {x.expect} in {len(diffs)} place(s), e.g. {diffs[:3]}", file=sys.stderr)
                return 1
        out = Path(x.out); out.parent.mkdir(parents=True, exist_ok=True)
        s.to_file(out, layer="sites", driver="GPKG")
        if labels is not None:
            labels.merge(s[["site_id", "split", "unit_id"]], on="site_id").to_file(out, layer="labels", driver="GPKG")
        ov.to_csv(out.with_name(out.stem + "_overlaps.csv"), index=False)
        out.with_name(out.stem + "_summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return 0 if summary["check"] else 1
    if x.cmd == "download-global-ml-ready":
        from .datasets import download_global_ml_ready
        print(download_global_ml_ready(x.out)); return 0
    if x.cmd == "get-sites":
        from .datasets import get_sites
        r = get_sites(x.sites_json, x.sites_gpkg, x.raw_root, x.prepared_root, drop_raw=not x.keep_raw,
                      log=lambda t: print(t, file=sys.stderr, flush=True))
        print(json.dumps(r, indent=2)); return 1 if r["failed"] else 0
    if x.cmd == "lock-test":
        import geopandas as gpd
        from .splits import lock_test
        lk = lock_test(gpd.read_file(x.split, layer="sites"), x.out, x.note)
        print(f"locked {lk['count']} test sites, sha256 {lk['sha256']}"); return 0
    if x.cmd == "check-split":
        import geopandas as gpd
        import pandas as pd
        from .splits import check_split, read_lock
        s = gpd.read_file(x.split, layer="sites")
        s = s.assign(split=s[x.split_column])
        ov = pd.read_csv(x.overlaps) if x.overlaps else None
        r = check_split(s.drop(columns=[c for c in ("unit_id",) if x.split_column != "split" and c in s]),
                        x.buffer_km, ov, read_lock(x.lock) if x.lock else None)
        print(json.dumps({k: (v if isinstance(v, bool) else len(v)) for k, v in r.items()}, indent=2))
        return 0 if r["ok"] else 1
    if x.cmd == "g1":
        from .baseline import run_g1
        from .negatives import PROXY_TYPES
        if x.hard_negatives_per_type and not x.worldcover_root:
            print("--hard-negatives-per-type needs --worldcover-root", file=sys.stderr); return 2
        hn = ({"worldcover_root": x.worldcover_root, "per_type": x.hard_negatives_per_type,
               "buffer_px": x.hard_negative_buffer_px,
               "types": [code for code, name in PROXY_TYPES.items() if name in x.hard_negative_types.split(",")]}
              if x.hard_negatives_per_type else None)
        m = run_g1(json.loads(Path(x.sites).read_text()), x.split, x.prepared_root, x.labels_root, x.out, x.seed,
                   log=lambda t: print(t, file=sys.stderr, flush=True), hard_negatives=hn,
                   worldcover_root=x.worldcover_root)
        for name, r in m["models"].items():
            a = r["test"]["strata"]["all"]
            print(f"{name}: test pixel F1 {a['f1']:.3f} (95% CI {a['ci95']['f1'][0]:.3f} to {a['ci95']['f1'][1]:.3f}), "
                  f"footprint recall {a['footprint_recall_any']:.3f}, false candidates/1000 km2 "
                  f"{a['false_candidates_per_1000km2']:.1f}")
        return 0
    if x.cmd == "g2":
        from .g2 import run_g2
        m = run_g2(json.loads(Path(x.sites).read_text()), x.split, x.prepared_root, x.labels_root, x.out, x.seed,
                   log=lambda t: print(t, file=sys.stderr, flush=True), worldcover_root=x.worldcover_root,
                   device=x.device, epochs=x.epochs, batch=x.batch, chips_per_site=x.chips_per_site, chip=x.chip,
                   workers=x.workers, lr=x.lr, encoder=x.encoder, encoder_weights=x.encoder_weights,
                   amp=False if x.no_amp else None, resume=x.resume, negatives_root=x.negatives_root,
                   init_from=x.init_from, negative_fraction=x.negative_fraction)
        a = m["models"]["unet"]["test"]["strata"]["all"]
        print(f"unet: test pixel F1 {a['f1']:.3f} (95% CI {a['ci95']['f1'][0]:.3f} to {a['ci95']['f1'][1]:.3f}), "
              f"false candidates/1000 km2 {a['false_candidates_per_1000km2']:.1f}. Compare with the G1 random forest: "
              f"minefoot compare --a <g1>/metrics.json:random_forest --b {x.out}/metrics.json:unet --split val")
        return 0
    if x.cmd == "mine-negatives":
        from .g2 import mine_negatives
        ids = json.loads(Path(x.sites).read_text())["train"]
        r = mine_negatives(x.run, ids, x.prepared_root, x.labels_root, x.out, x.worldcover_root, x.buffer_px,
                           log=lambda t: print(t, file=sys.stderr, flush=True))
        print(json.dumps(r["total"], indent=2)); return 0
    if x.cmd == "rasterize-negatives":
        from .g2 import rasterize_negatives
        ids = json.loads(Path(x.sites).read_text())["train"]
        print(json.dumps(rasterize_negatives(x.gpkg, ids, x.prepared_root, x.out), indent=2)); return 0
    if x.cmd == "false-alerts":
        from .negatives import evaluate_run_by_type
        r = evaluate_run_by_type(x.run, x.model, x.split, x.prepared_root, x.labels_root, x.worldcover_root)
        if x.out:
            Path(x.out).write_text(json.dumps(r, indent=2))
        for t, v in r["by_type"].items():
            if v["rate"] is None:
                print(f"  {t:12s} no pixels"); continue
            print(f"  {t:12s} false-alert rate {v['rate']:.3%} (95% CI {v['ci95'][0]:.3%} to {v['ci95'][1]:.3%}), "
                  f"{v['pixels']:,} px on {v['sites_with_type']} sites")
        return 0
    if x.cmd == "negatives-template":
        from .negatives import write_template
        p = write_template(x.out); print(p); print(p.with_suffix(".qml")); return 0
    if x.cmd == "negatives-check":
        from .negatives import check_negatives
        r = check_negatives(x.path); print(json.dumps(r, indent=2)); return 1 if r["problems"] else 0
    if x.cmd == "error-gallery":
        from .viewer import error_gallery
        print(error_gallery(x.run, x.model, x.prepared_root, x.out, x.split, x.limit)); return 0
    if x.cmd == "rescore":
        from .baseline import rescore_run
        m = rescore_run(x.run, x.prepared_root, x.labels_root, log=lambda t: print(t, file=sys.stderr, flush=True))
        for name, r in m["models"].items():
            for k in ("val", "test"):
                a = r[k]["strata"]["all"]
                print(f"{name} {k}: false candidates/1000 km2 {a['false_candidates_per_1000km2']:.1f} "
                      f"(touching no label: {a['false_candidates_detached_per_1000km2']:.1f}), "
                      f"false-candidate area {a['false_candidate_ha_per_1000km2']:.0f} ha/1000 km2")
        return 0
    if x.cmd == "conditions-eval":
        from .conditions import evaluate
        ids = json.loads(Path(x.sites).read_text())
        ids = ids["val"] + ids["test"] if isinstance(ids, dict) else ids
        print(json.dumps({k: v for k, v in evaluate(x.prepared_root, x.worldcover_root, ids, x.out, x.labels_root).items() if k != "site_ids"}, indent=1))
        return 0
    if x.cmd == "ingest-pool":
        from .pool import load_pool_export, write_pool
        sites, labels, negs, rep = load_pool_export(x.zip, x.locked_split, x.test_regions, version=x.version)
        print(write_pool(sites, labels, negs, rep, x.out)); print(json.dumps(rep, indent=2)); return 0
    if x.cmd == "curves":
        from .baseline import run_curves
        c = run_curves(x.run, x.prepared_root, x.labels_root, log=lambda t: print(t, file=sys.stderr, flush=True))
        for name, r in c["models"].items():
            for k in ("val", "test"):
                a = r[k]["at_threshold"]
                print(f"{name} {k}: at threshold {r['threshold_score']:.2f} precision {a['precision']:.3f} recall {a['recall']:.3f} "
                      f"(matches the run record)")
        print(Path(x.run) / "curves.json"); return 0
    if x.cmd == "labelling-kit":
        from .labelling_kit import build_kit
        r = build_kit(x.config, x.out, log=lambda t: print(t, file=sys.stderr, flush=True))
        print(json.dumps(r, indent=2)); print(Path(x.out) / "project.qgs"); return 0
    if x.cmd == "labels-check":
        from .labelling_kit import check_labels
        r = check_labels(x.path, x.kit); print(json.dumps(r, indent=2)); return 1 if r["problems"] else 0
    if x.cmd == "labels-progress":
        from .labelling_kit import review_progress
        r = review_progress(x.path, x.kit)
        print(f"{'site':<16}{'polygons':>9}{'draft':>7}{'mining ha':>11}  scale")
        for s in r["sites"]:
            print(f"{s['site_id']:<16}{s['polygons']:>9}{s['draft']:>7}{s['mining_ha']:>11}  {','.join(s['mine_scale'])}"
                  + ("" if s["draft"] else "  reviewed"))
        print(f"\n{r['reviewed_sites']} of {r['total_sites']} sites reviewed. "
              + ("Ready to lock." if r["ready_to_lock"] else "Not ready to lock: " + "; ".join(r["blockers"])))
        return 0 if r["ready_to_lock"] else 1
    if x.cmd == "lock-labels":
        from .labelling_kit import lock_labels
        lk = lock_labels(x.path, x.out, x.kit, x.note)
        print(f"locked {lk['count']} Nigerian sites, sha256 {lk['sha256']}"); return 0
    if x.cmd == "verify-lock":
        from .labelling_kit import verify_lock
        r = verify_lock(x.path, x.lock); print(json.dumps(r, indent=2)); return 0 if r["intact"] else 1
    if x.cmd == "g3":
        from .g3 import run_g3
        r = run_g3(x.run, x.kit, x.labels, x.lock, x.out, x.negatives, x.window_m, x.access_log, x.device,
                   log=lambda t: print(t, file=sys.stderr, flush=True))
        a = r["strata"]["all"]; sr = r["site_recall"]
        print(f"G3 on {r['sites']} locked Nigerian sites: pixel F1 {a['f1']:.3f} "
              f"(95% CI {a['ci95']['f1'][0]:.3f} to {a['ci95']['f1'][1]:.3f}); global test F1 {r['global_test']['f1']:.3f}; "
              f"gap {r['gap_f1_nigeria_minus_global']:+.3f}")
        if sr["found_ci95"]:
            print(f"site recall (sites >= 1 ha, any overlap): {sr['found_any_overlap']:.2f} "
                  f"(95% CI {sr['found_ci95'][0]:.2f} to {sr['found_ci95'][1]:.2f}); FRD O1 target 0.70")
        return 0
    if x.cmd == "compare":
        from .baseline import paired_compare
        def rows(spec):
            path, model = spec.rsplit(":", 1)
            r = json.loads(Path(path).read_text())["models"][model][x.split]["per_site"]
            if x.stratum:
                col, val = x.stratum.split("=", 1)
                r = [row for row in r if str(row.get(col)) == val]
            return r
        res = paired_compare(rows(x.a), rows(x.b), n_boot=x.n_boot)
        res.update(a=x.a, b=x.b, split=x.split, stratum=x.stratum)
        if x.out:
            Path(x.out).write_text(json.dumps(res, indent=2))
        print(f"B = {x.b}\nA = {x.a}\n{x.split}, {res['sites']} sites, paired site bootstrap ({x.n_boot})")
        for m, v in res["metrics"].items():
            print(f"  {m:30s} A {v['a']:8.3f}  B {v['b']:8.3f}  B-A {v['b_minus_a']:+8.3f} "
                  f"(95% CI {v['ci95'][0]:+.3f} to {v['ci95'][1]:+.3f})  {v['verdict']}")
        return 0
    if x.cmd == "demo":
        from .preprocess import prepare_scene
        from .synthetic import make_scene
        from .viewer import build_gallery
        out = Path(x.out); dirs, labs = [], {}
        for kw in (dict(baseline="05.10", offset=-1000), dict(baseline="03.01", offset=0, seed=7)):
            raw = make_scene(out / "raw", **kw)
            d = out / "prepared" / raw.name
            prepare_scene(raw, d); dirs.append(d); labs[raw.name] = raw / "truth.geojson"
        print(build_gallery(dirs, out / "view", labs, "Pipeline demo on synthetic scenes (not real imagery)")); return 0


if __name__ == "__main__":
    raise SystemExit(main())
