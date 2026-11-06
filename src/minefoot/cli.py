"""Command line: doctor, search, fetch, prepare, view, inspect-dataset, ingest, fetch-sites, label-audit, mask-report, demo."""
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
    a.add_argument("--provider", choices=list(PROVIDERS), help=prov_help)
    a = sub.add_parser("fetch"); a.add_argument("--bbox", type=_bbox, required=True); a.add_argument("--start", required=True)
    a.add_argument("--end", required=True); a.add_argument("--max-cloud", type=float, default=40)
    a.add_argument("--provider", choices=list(PROVIDERS), help=prov_help)
    a.add_argument("--scenes", type=int, default=1); a.add_argument("--scene-id"); a.add_argument("--out", default="data/raw")
    a = sub.add_parser("prepare"); a.add_argument("raw"); a.add_argument("--out", default="data/prepared")
    a = sub.add_parser("view"); a.add_argument("prepared", nargs="+"); a.add_argument("--labels"); a.add_argument("--out", default="data/view")
    a = sub.add_parser("inspect-dataset"); a.add_argument("path")
    a = sub.add_parser("ingest", help="convert an external dataset into the common site schema")
    a.add_argument("dataset", choices=["global-ml-ready"]); a.add_argument("path")
    a.add_argument("--label-layer", default="preferred_polygons"); a.add_argument("--out", default="data/datasets/global_ml_ready")
    a = sub.add_parser("fetch-sites", help="fetch each site's exact scene and labels")
    a.add_argument("sites", help="sites.gpkg written by ingest"); a.add_argument("--n", type=int, default=50)
    a.add_argument("--site-id", action="append", help="fetch only these sites (repeatable)")
    a.add_argument("--seed", type=int, default=0); a.add_argument("--provider", choices=list(PROVIDERS))
    a.add_argument("--out", default="data/raw/global_ml_ready")
    a = sub.add_parser("label-audit", help="how much labelled ground the quality mask removes, per scene")
    a.add_argument("prepared", nargs="+"); a.add_argument("--raw-root", required=True,
                   help="folder holding <scene>/labels.geojson"); a.add_argument("--csv")
    a = sub.add_parser("mask-report", help="share of each polygon (candidate, label, area) the quality mask removed")
    a.add_argument("prepared"); a.add_argument("--polygons", required=True)
    a.add_argument("--out", required=True, help="output .gpkg; a .csv is written next to it")
    a = sub.add_parser("demo"); a.add_argument("--out", default="data/demo")
    x = ap.parse_args(argv)

    if x.cmd == "doctor":
        from .catalog import PROVIDERS, _client, summarise
        ok = False
        for p, c in PROVIDERS.items():
            try:
                items = list(_client(p).search(collections=[c["collection"]], bbox=[8.8, 9.7, 8.95, 9.85], max_items=1,
                                               datetime="2026-01-01/2026-02-28").items())
                if not items:
                    print(f"{p}: reachable, no scene found"); continue
                missing = [v for v in c["assets"].values() if v not in items[0].assets]
                from .reflectance import resolve
                r = resolve(items[0].properties, items[0].assets[c["assets"]["B04"]].to_dict())
                print(f"{p}: OK {items[0].id} baseline={items[0].properties.get('s2:processing_baseline')} "
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
        labs = {Path(d).name: x.labels for d in x.prepared} if x.labels else None
        print(build_gallery(x.prepared, x.out, labs)); return 0
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
