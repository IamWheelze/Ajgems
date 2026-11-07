"""Inspection package for any step's output (FR-133, FR-121 to FR-123, FR-128)."""
from __future__ import annotations
import base64, html, io, json
from pathlib import Path
import numpy as np
import rasterio
from PIL import Image
from .schema import BANDS

# Fixed stretches (FR-122): the same reflectance always looks the same across scenes.
STRETCH = {"true_colour": (("B04", "B03", "B02"), (0.0, 0.35)),
           "swir_false_colour": (("B12", "B8A", "B04"), (0.0, 0.50)),
           "nir_false_colour": (("B08", "B04", "B03"), (0.0, 0.50))}
SCL_COLOURS = {0: (0, 0, 0), 1: (120, 0, 120), 3: (70, 70, 110), 8: (200, 200, 200), 9: (255, 255, 255),
               10: (150, 210, 240), 11: (255, 150, 0)}
LAYER_TEXT = {
    "true_colour": ("True colour", "B4, B3, B2. Fixed stretch 0 to 0.35."),
    "swir_false_colour": ("SWIR false colour", "B12, B8A, B4. Bare ground and disturbance stand out from vegetation."),
    "nir_false_colour": ("NIR false colour", "B8, B4, B3. Healthy vegetation looks red."),
    "ndvi": ("NDVI", "Dark is low vegetation, light is high. Scale -0.2 to 0.8."),
    "ndwi": ("NDWI", "Light is water-like. Scale -0.6 to 0.6."),
    "quality": ("Quality mask", "Unusable pixels: cloud in grey and white, shadow in slate, no data in black. Bright pixels "
                "classed as snow in orange. Bright mine ground is often classed as cloud, so check grey inside outlines."),
    "labels": ("Polygons on imagery", "Polygon outlines on the true-colour view; masked pixels inside them in magenta."),
}
BADGE = {"pass": "Passes coverage check", "warn": "Warning: low usable coverage", "block": "Blocked: too little usable imagery"}


def rgb(arr, names, layers, lo_hi):
    lo, hi = lo_hi
    idx = [names.index(b) for b in layers]
    out = np.clip((arr[idx] - lo) / (hi - lo), 0, 1)
    nan = ~np.isfinite(out).all(axis=0)
    out = np.nan_to_num(out, nan=0.0)
    img = (out.transpose(1, 2, 0) * 255).astype("uint8")
    img[nan] = (45, 45, 45)   # unusable pixels are never drawn as clean ground
    return img


def ramp(values, lo, hi):
    v = np.clip((values - lo) / (hi - lo), 0, 1)
    nan = ~np.isfinite(v)
    g = (np.nan_to_num(v) * 255).astype("uint8")
    img = np.stack([g] * 3, axis=2)
    img[nan] = (120, 30, 90)
    return img


def normdiff(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        d = a + b
        return np.where(np.abs(d) > 1e-6, (a - b) / d, np.nan)


def outline(mask):
    m = mask.astype(bool)
    inner = m[1:-1, 1:-1] & m[:-2, 1:-1] & m[2:, 1:-1] & m[1:-1, :-2] & m[1:-1, 2:]
    edge = m.copy()
    edge[1:-1, 1:-1] &= ~inner
    return edge


def build_layers(arr, scl, label=None) -> dict[str, np.ndarray]:
    from .quality import usable_mask
    names = list(BANDS)
    out = {k: rgb(arr, names, b, s) for k, (b, s) in STRETCH.items()}
    g = lambda b: arr[names.index(b)]
    out["ndvi"] = ramp(normdiff(g("B08"), g("B04")), -0.2, 0.8)
    out["ndwi"] = ramp(normdiff(g("B03"), g("B08")), -0.6, 0.6)
    q = out["true_colour"].copy() // 2
    for k, c in SCL_COLOURS.items():
        q[scl == k] = c
    out["quality"] = q
    if label is not None:
        l = out["true_colour"].copy()
        l[label.astype(bool) & ~usable_mask(scl)] = (230, 0, 200)   # inside a polygon but not seen
        l[outline(label)] = (0, 230, 255)
        out["labels"] = l
    return out


def png_uri(img, longest=720):
    im = Image.fromarray(img)
    if max(im.size) > longest:
        f = longest / max(im.size)
        im = im.resize((round(im.width * f), round(im.height * f)), Image.NEAREST)
    b = io.BytesIO()
    im.save(b, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def band_stats(arr):
    names = list(BANDS)
    rows = []
    for i, b in enumerate(names):
        v = arr[i][np.isfinite(arr[i])]
        if v.size == 0:
            rows.append({"band": b, "n": 0}); continue
        p = np.percentile(v, [1, 50, 99])
        rows.append({"band": b, "n": int(v.size), "p1": float(p[0]), "median": float(p[1]), "p99": float(p[2]),
                     "negative_pct": float((v < 0).mean() * 100), "over1_pct": float((v > 1).mean() * 100)})
    return rows


def checks(stats, cov, meta, label=None, mask_summary=None) -> list[tuple[str, str]]:
    """Automatic warnings shown at the top of each scene."""
    out = []
    if meta.get("synthetic"):
        out.append(("info", "Synthetic test scene. Not real imagery. It says nothing about real sites."))
    if cov["decision"] != "pass":
        out.append(("warn", f"{BADGE[cov['decision']]} ({cov['usable_fraction']:.0%} usable)."))
    if any(r.get("negative_pct", 0) > 5 for r in stats):
        out.append(("warn", "Over 5% negative reflectance in a band. Check the Sentinel-2 offset."))
    if any(r.get("median", 0) > 0.6 for r in stats):
        out.append(("warn", "Median reflectance above 0.6 in a band. Check the offset or heavy cloud."))
    if cov["suspect_bright_fraction"] > 0.001:
        out.append(("warn", f"{cov['suspect_bright_fraction']:.1%} of pixels classed as snow. In Nigeria this suggests "
                            "bright ground such as tailings. Those pixels are kept for review."))
    if mask_summary and mask_summary["pixels"]:
        st = mask_summary["by_status"]
        hit = st.get("partly_masked", 0) + st.get("mostly_masked", 0)
        if hit:
            out.append(("warn", f"{hit} of {mask_summary['polygons']} polygon(s) partly or mostly masked: "
                                f"{mask_summary['masked_fraction']:.1%} of pixels inside polygons were not usable "
                                f"({mask_summary['cloud_fraction']:.1%} classed as cloud), although the scene "
                                f"{'passes' if cov['decision'] == 'pass' else 'was checked'} overall. Those pixels were not assessed."))
    if label is not None and not label.any():
        out.append(("warn", "Label file has no polygon inside this image."))
    return out


def histogram_svg(arr) -> str:
    """Small multiples, one per band, on one shared reflectance axis."""
    W, H, cells = 148, 74, []
    edges = np.linspace(-0.1, 0.7, 41)
    for i, b in enumerate(BANDS):
        v = arr[i][np.isfinite(arr[i])]
        counts, _ = np.histogram(v, edges)
        m = max(int(counts.max()), 1)
        bars = []
        for j, c in enumerate(counts):
            bh = (c / m) * (H - 22)
            bars.append(f'<rect x="{j * (W / 40) + .3:.1f}" y="{H - 14 - bh:.1f}" width="{W / 40 - .6:.2f}" '
                        f'height="{bh:.1f}" rx="1"><title>{b}: {edges[j]:.2f} to {edges[j + 1]:.2f}, {int(c):,} pixels</title></rect>')
        zero = (0.1 / 0.8) * W
        cells.append(f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{b} reflectance histogram">'
                     f'<text x="0" y="9" class="h">{b}</text>'
                     f'<line x1="{zero:.1f}" x2="{zero:.1f}" y1="10" y2="{H - 14}" class="z"/>'
                     f'<line x1="0" x2="{W}" y1="{H - 14}" y2="{H - 14}" class="a"/>'
                     f'<g class="b">{"".join(bars)}</g>'
                     f'<text x="{zero:.1f}" y="{H - 3}" class="t" text-anchor="middle">0</text>'
                     f'<text x="{W}" y="{H - 3}" class="t" text-anchor="end">0.7</text></svg>')
    return '<div class="hist">' + "".join(cells) + "</div>"


CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d1d1b;--muted:#5d5c57;--line:#d9d7cf;--bar:#2a6f97;--warn:#8a5a00;--warnbg:#fdf3dc;--info:#17557a;--infobg:#e3f0f7}
@media (prefers-color-scheme:dark){:root{--bg:#191917;--card:#232321;--ink:#eeede8;--muted:#a9a79f;--line:#3b3a36;--bar:#74b3d8;--warn:#f2c572;--warnbg:#3a2e14;--info:#a5d5f0;--infobg:#17303e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif}
main{max-width:1180px;margin:auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:0 0 4px;overflow-wrap:anywhere}h3{font-size:14px;margin:0}
.sub{color:var(--muted);margin:0 0 20px}.scene{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:24px}
.meta{color:var(--muted);font-size:13px;margin:0 0 12px;overflow-wrap:anywhere}.note{border-radius:6px;padding:8px 12px;margin:6px 0;font-size:14px}
.note.warn{background:var(--warnbg);color:var(--warn)}.note.info{background:var(--infobg);color:var(--info)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;margin-top:14px}figure{margin:0}figure img{width:100%;display:block;border-radius:6px;image-rendering:pixelated}
figcaption{font-size:12.5px;color:var(--muted);padding-top:5px}figcaption b{display:block;color:var(--ink);font-size:14px}
.hist{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:8px}.hist svg{width:100%;height:auto}
.hist .b{fill:var(--bar)}.hist .a{stroke:var(--line)}.hist .z{stroke:var(--muted);stroke-dasharray:2 2;stroke-width:.6}.hist .h{font-size:9px;font-weight:600;fill:var(--ink)}.hist .t{font-size:7.5px;fill:var(--muted)}
details{margin-top:14px}summary{cursor:pointer;font-weight:600}.scroll{overflow-x:auto}table{border-collapse:collapse;font-size:13px;margin-top:8px;min-width:520px}th,td{padding:4px 12px 4px 0;border-bottom:1px solid var(--line);text-align:right}th:first-child,td:first-child{text-align:left}
"""


def render_scene(prepared_dir, labels=None) -> tuple[str, dict]:
    p = Path(prepared_dir)
    meta = json.loads((p / "prepared.json").read_text())
    with rasterio.open(p / "reflectance.tif") as s:
        arr = s.read()
    with rasterio.open(p / "scl.tif") as s:
        scl = s.read(1)
    label = report = summary = None
    if labels is not None:
        from .labels import polygon_mask_report, read_vectors, rasterize_to, summarise_mask_report
        gdf = read_vectors(labels)
        label = rasterize_to(p / "reflectance.tif", gdf)
        report = polygon_mask_report(p, gdf)
        summary = summarise_mask_report(report) if len(report) else None
    layers = build_layers(arr, scl, label)
    cov = meta["coverage"]
    stats = band_stats(arr)
    notes = checks(stats, cov, meta, label, summary)
    figs = "".join(f'<figure><img loading="lazy" alt="{html.escape(LAYER_TEXT[k][0])}" src="{png_uri(v)}">'
                   f'<figcaption><b>{html.escape(LAYER_TEXT[k][0])}</b>{html.escape(LAYER_TEXT[k][1])}</figcaption></figure>'
                   for k, v in layers.items())
    def row(r):
        if not r["n"]:
            return f"<tr><td>{r['band']}</td><td colspan=6>no usable pixels</td></tr>"
        return (f"<tr><td>{r['band']}</td><td>{r['n']:,}</td><td>{r['p1']:.3f}</td><td>{r['median']:.3f}</td>"
                f"<td>{r['p99']:.3f}</td><td>{r['negative_pct']:.2f}</td><td>{r['over1_pct']:.2f}</td></tr>")
    rad = meta["radiometry"][BANDS[0]]
    body = (f'<section class="scene"><h2>{html.escape(meta["scene_id"])}</h2>'
            f'<p class="meta">Date {html.escape(str(meta.get("datetime")))} · Source {html.escape(str(meta.get("provider")))} · '
            f'Baseline {html.escape(str(meta.get("processing_baseline")))} · Offset {rad["offset_dn"]} DN ({html.escape(rad["rule"])}) · '
            f'Usable {cov["usable_fraction"]:.0%} · {html.escape(BADGE[cov["decision"]])}</p>'
            + "".join(f'<div class="note {k}">{html.escape(t)}</div>' for k, t in notes)
            + f'<div class="grid">{figs}</div><details open><summary>Reflectance by band</summary>{histogram_svg(arr)}'
            '<div class="scroll"><table><tr><th>Band</th><th>Usable pixels</th><th>1st pct</th><th>Median</th><th>99th pct</th>'
            f'<th>Negative %</th><th>Over 1 %</th></tr>{"".join(row(r) for r in stats)}</table></div></details>'
            + mask_table(report) + '</section>')
    return body, {"layers": layers, "stats": stats, "checks": notes, "meta": meta, "mask_report": report,
                  "mask_summary": summary}


def mask_table(report, limit=15) -> str:
    """Per-polygon masking, most masked first."""
    if report is None or not len(report):
        return ""
    r = report[report["pixels"] > 0].sort_values("masked_fraction", ascending=False)
    pct = lambda v: f"{v:.1%}"
    rows = "".join(f"<tr><td>{i + 1}</td><td>{int(x.pixels):,}</td><td>{pct(x.masked_fraction)}</td>"
                   f"<td>{pct(x.cloud_fraction)}</td><td>{pct(x.shadow_fraction)}</td><td>{pct(x.snow_fraction)}</td>"
                   f"<td>{html.escape(x.mask_status.replace('_', ' '))}</td></tr>"
                   for i, x in enumerate(r.head(limit).itertuples()))
    outside = int((report["pixels"] == 0).sum())
    more = f" Showing {limit} of {len(r)}." if len(r) > limit else ""
    extra = f" {outside} polygon(s) fall outside this image." if outside else ""
    return ('<details><summary>Masking inside polygons</summary><p class="meta">Share of each polygon\'s pixels the quality '
            f'mask removed, most masked first.{more}{extra} Thresholds are provisional.</p><div class="scroll"><table>'
            '<tr><th>#</th><th>Pixels</th><th>Masked</th><th>Cloud</th><th>Shadow</th><th>Snow</th><th>Status</th></tr>'
            f'{rows}</table></div></details>')


def _to_cog(src_path, dst_path):
    """Copy a GeoTIFF as a cloud-optimised GeoTIFF (FR-133)."""
    from rasterio.shutil import copy as rio_copy
    rio_copy(src_path, dst_path, driver="COG", compress="DEFLATE", overview_resampling="nearest")


def export_gis(prepared_dir, layers, out_dir, labels=None, mask_report=None):
    """Cloud-optimised GeoTIFFs and a GeoPackage that open directly in QGIS.

    Writes the display layers (8-bit RGB, fixed stretches), reflectance.tif (all
    bands, for the pixel inspector) and scl.tif (scene classes), all as COGs.
    """
    import tempfile
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    prepared_dir = Path(prepared_dir)
    with rasterio.open(prepared_dir / "reflectance.tif") as s:
        prof = dict(driver="GTiff", height=s.height, width=s.width, crs=s.crs, transform=s.transform, dtype="uint8",
                    count=3, compress="deflate", tiled=True, photometric="RGB")
    with tempfile.TemporaryDirectory() as tmp:
        for k, img in layers.items():
            t = Path(tmp) / f"{k}.tif"
            with rasterio.open(t, "w", **prof) as d:
                d.write(img.transpose(2, 0, 1))
            _to_cog(t, out / f"{k}.tif")
    for name in ("reflectance.tif", "scl.tif"):
        _to_cog(prepared_dir / name, out / name)
    # Polygons are written in the scene's CRS, so every layer in the package shares it.
    if mask_report is not None:  # polygons with their masking columns, ready to style by mask_status
        mask_report.to_crs(prof["crs"]).to_file(out / "labels.gpkg", driver="GPKG")
    elif labels is not None:
        from .labels import read_vectors
        read_vectors(labels).to_crs(prof["crs"]).to_file(out / "labels.gpkg", driver="GPKG")


def build_gallery(prepared_dirs, out_dir, labels: dict | None = None, title="Scene inspection") -> Path:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    sections, summary, project_scenes = [], [], []
    for d in prepared_dirs:
        lab = (labels or {}).get(Path(d).name)
        body, info = render_scene(d, lab)
        with rasterio.open(Path(d) / "reflectance.tif") as s:
            epsg = s.crs.to_epsg()
        project_scenes.append({"name": Path(d).name, "title": f'{info["meta"]["scene_id"]} ({Path(d).name})',
                               "epsg": epsg, "has_polygons": lab is not None,
                               "has_mask_status": info["mask_report"] is not None})
        sections.append(body)
        export_gis(d, info["layers"], out / "gis" / Path(d).name, lab, info["mask_report"])
        summary.append({"scene_id": info["meta"]["scene_id"], "coverage": info["meta"]["coverage"],
                        "polygon_masking": info["mask_summary"], "bands": info["stats"], "checks": info["checks"]})
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main><h1>{html.escape(title)}</h1>'
            f'<p class="sub">{len(sections)} scene(s). Hover a histogram bar for its pixel count. '
            'Dark grey (or purple in NDVI and NDWI) means no usable data, not clean ground.</p>' + "".join(sections) + "</main></body></html>")
    (out / "gallery.html").write_text(page)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    from .qgis_project import write_project
    write_project(out, project_scenes, title)
    return out / "gallery.html"


ERROR_COLOURS = {1: (0, 200, 90), 2: (255, 140, 0), 3: (220, 0, 120), 4: (90, 90, 90)}


def jpg_uri(img, longest=560, quality=82):
    """JPEG data URI: much smaller than PNG for imagery views; not for masks or exact colours."""
    im = Image.fromarray(img)
    if max(im.size) > longest:
        f = longest / max(im.size)
        im = im.resize((round(im.width * f), round(im.height * f)), Image.BILINEAR)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def error_gallery(run_dir, model, prepared_root, out_path, split="test", limit=20, longest=560) -> Path:
    """Evaluation error gallery ranked by worst pixel F1 (FR-129). Self-contained HTML.

    Uses the run's stored error maps (written for test sites) and the prepared
    reflectance for the true-colour view.
    """
    run_dir, out_path = Path(run_dir), Path(out_path)
    m = json.loads((run_dir / "metrics.json").read_text())["models"][model]
    rows = m[split]["per_site"]
    def f1(r):
        d = 2 * r["tp"] + r["fp"] + r["fn"]
        return 2 * r["tp"] / d if d else float("nan")
    rows = sorted(rows, key=lambda r: (f1(r) if f1(r) == f1(r) else -1))
    cards = []
    for r in rows[:limit]:
        sid = r["site_id"]
        emap = run_dir / "error_maps" / f"{sid}__{model}.tif"
        if not emap.exists():
            continue
        with rasterio.open(Path(prepared_root) / sid / "reflectance.tif") as s:
            arr = s.read()
        tc = rgb(arr, list(BANDS), *STRETCH["true_colour"])
        with rasterio.open(emap) as s:
            e = s.read(1)
        ov = tc.copy()
        for k, c in ERROR_COLOURS.items():
            sel = e == k
            ov[sel] = (0.45 * ov[sel] + 0.55 * np.array(c)).astype("uint8")
        p = r["tp"] / (r["tp"] + r["fp"]) if r["tp"] + r["fp"] else float("nan")
        rc = r["tp"] / (r["tp"] + r["fn"]) if r["tp"] + r["fn"] else float("nan")
        cards.append(
            f'<section class="scene"><h2>{html.escape(sid)} · F1 {f1(r):.2f}</h2>'
            f'<p class="meta">{html.escape(str(r.get("mine_scale")))} · {html.escape(str(r.get("mine_type")))} · '
            f'{html.escape(str(r.get("region")))} · precision {p:.2f} · recall {rc:.2f} · '
            f'labelled footprints {r["label_footprints"]}, covered ≥25%: {r["covered_25pct"]} · '
            f'false candidates: {r["false_candidates"]} · labelled pixels not assessed: '
            f'{(r["label_pixels_masked"] / r["label_pixels"] if r["label_pixels"] else 0):.1%}</p>'
            f'<div class="grid"><figure><img alt="True colour" src="{jpg_uri(tc, longest)}"><figcaption><b>True colour</b>'
            f'B4, B3, B2, fixed stretch.</figcaption></figure><figure><img alt="Errors" src="{jpg_uri(ov, longest)}">'
            f'<figcaption><b>Errors</b>Green hit, orange false alarm, magenta missed, grey not assessed.</figcaption>'
            f'</figure></div></section>')
    legend = "".join(f'<span class="key" style="background:rgb{c}"></span>{t} '
                     for c, t in ((ERROR_COLOURS[1], "hit"), (ERROR_COLOURS[2], "false alarm"),
                                  (ERROR_COLOURS[3], "missed"), (ERROR_COLOURS[4], "not assessed")))
    title = f"Error gallery: {model}, {split}"
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{CSS}.key{{display:inline-block;width:12px;height:12px;border-radius:2px;'
            f'margin:0 4px 0 10px;vertical-align:-1px}}</style></head><body><main><h1>{html.escape(title)}</h1>'
            f'<p class="sub">Run {html.escape(run_dir.name)}. Internal research on Global ML-ready labels, which are not '
            f'Nigerian and miss some mining. Model score threshold {m["threshold_score"]:.2f}, chosen on validation. '
            f'Worst {len(cards)} of {len(rows)} sites by pixel F1.<br>{legend}</p>' + "".join(cards) + "</main></body></html>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page)
    return out_path
