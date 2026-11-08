"""Nigerian labelling kit (step 7, FRD 8.2, FR-073, FR-077, ML-07).

`build_kit` fetches the clearest dry-season Sentinel-2 scene covering each region,
prepares it through the one preprocessing path, and writes a QGIS project with the
imagery and two editable layers: mining labels and hard negatives. People digitise
there, following docs/labelling_protocol.md.

`check_labels` names problems in the mining-label layer. `lock_labels` freezes the
labelled sites as the locked Nigerian test set: it records a hash of every site's
geometry and attributes, so any later edit is detected (`verify_lock`). G3 uses
these sites for testing only; nothing here trains on them.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

LABEL_LAYER = "mining_labels"
LABEL_CLASSES = ("mining_disturbance", "ignore")
MINE_SCALES = ("artisanal", "industrial", "unknown")
LABEL_FIELDS = {"site_id": "str", "label_class": "str", "mine_scale": "str", "protocol_version": "str",
                "scene_id": "str", "image_date": "str", "annotator": "str", "evidence_grade": "str", "notes": "str"}
SITE_ID = re.compile(r"^ng-[a-z0-9_]+-\d{3}$")


# ---------- templates ----------

def _value_map(field, values):
    opts = "".join(f'<Option type="Map"><Option name="{v}" type="QString" value="{v}"/></Option>' for v in values)
    return (f'<field name="{field}"><editWidget type="ValueMap"><config><Option type="Map">'
            f'<Option name="map" type="List">{opts}</Option></Option></config></editWidget></field>')


def label_form(protocol_version="0.1") -> str:
    from .negatives import EVIDENCE_GRADES
    return ("<fieldConfiguration>" + _value_map("label_class", LABEL_CLASSES) + _value_map("mine_scale", MINE_SCALES)
            + _value_map("evidence_grade", EVIDENCE_GRADES) + "</fieldConfiguration><defaults>"
            f'<default field="protocol_version" expression="\'{protocol_version}\'" applyOnUpdate="0"/>'
            '<default field="label_class" expression="\'mining_disturbance\'" applyOnUpdate="0"/>'
            '<default field="annotator" expression="@user_full_name" applyOnUpdate="0"/></defaults>')


def write_label_template(path, protocol_version="0.1") -> Path:
    """Empty polygon GeoPackage for mining labels, plus a QGIS style with drop-down fields."""
    import geopandas as gpd
    import pandas as pd
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    cols = {k: pd.Series(dtype="object") for k in LABEL_FIELDS}
    from .negatives import write_empty_polygon_layer
    write_empty_polygon_layer(path, LABEL_LAYER, list(LABEL_FIELDS))
    path.with_suffix(".qml").write_text("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
                                        f'<qgis version="3.34.0">{label_form(protocol_version)}</qgis>\n')
    return path


# ---------- kit ----------

def _choose_scene(items, bbox):
    """Lowest-cloud scene whose footprint covers the box; else the one covering most of it."""
    from shapely.geometry import box, shape
    b = box(*bbox)
    scored = []
    for it in items:
        cover = shape(it.geometry).intersection(b).area / b.area
        scored.append((cover < 0.999, -round(cover, 3), it.properties.get("eo:cloud_cover", 100), it.id, it))
    scored.sort(key=lambda t: t[:4])
    return scored[0][4], -scored[0][1]


def build_kit(config_path, out_dir, log=print) -> dict:
    """Fetch, prepare and package every region in the config. Re-running skips finished regions."""
    import yaml
    from .catalog import fetch_scene, search
    from .preprocess import prepare_scene
    from .qgis_project import write_project
    from .negatives import write_template
    from .viewer import build_layers, _to_cog
    import rasterio
    import tempfile
    cfg = yaml.safe_load(Path(config_path).read_text())
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    report, scenes = {"config": str(config_path), "regions": {}}, []
    for r in cfg["regions"]:
        name, bbox = r["name"], r["bbox"]
        gis = out / "gis" / name
        prep = out / "prepared" / name
        if not (prep / "prepared.json").exists():
            if r.get("scene_id"):   # pinned, so every machine builds the kit from the same scene
                from .catalog import get_item
                prov = r["provider"]; item = get_item(prov, r["scene_id"]); cover = None
                log(f"{name}: pinned scene {item.id} from {prov}")
            else:
                prov, items = search(bbox, r["start"], r["end"], r.get("max_cloud", 20))
                if not items:
                    raise LookupError(f"{name}: no scene under {r.get('max_cloud', 20)}% cloud in {r['start']} to {r['end']}")
                item, cover = _choose_scene(items, bbox)
                log(f"{name}: {item.id} from {prov}, cloud {item.properties.get('eo:cloud_cover')}%, covers {cover:.0%} of box")
            raw = fetch_scene(item, prov, bbox, out / "raw", name=name)
            prepare_scene(raw, prep)
            (raw / "raw_dn.tif").unlink(missing_ok=True)   # prepared data is all the kit and G3 need
        meta = json.loads((prep / "prepared.json").read_text())
        if not (gis / "true_colour.tif").exists():
            with rasterio.open(prep / "reflectance.tif") as s:
                arr = s.read(); prof = s.profile
            with rasterio.open(prep / "scl.tif") as s:
                scl = s.read(1)
            layers = build_layers(arr, scl)
            gis.mkdir(parents=True, exist_ok=True)
            p = dict(driver="GTiff", height=prof["height"], width=prof["width"], crs=prof["crs"],
                     transform=prof["transform"], dtype="uint8", count=3, compress="deflate", tiled=True, photometric="RGB")
            with tempfile.TemporaryDirectory() as tmp:
                for k, img in layers.items():
                    t = Path(tmp) / f"{k}.tif"
                    with rasterio.open(t, "w", **p) as d:
                        d.write(img.transpose(2, 0, 1))
                    _to_cog(t, gis / f"{k}.tif")
            for f in ("reflectance.tif", "scl.tif"):
                _to_cog(prep / f, gis / f)
            log(f"{name}: display layers written")
        with rasterio.open(gis / "true_colour.tif") as s:
            epsg = s.crs.to_epsg()
        cov = meta["coverage"]
        report["regions"][name] = {"scene_id": meta["scene_id"], "date": meta["datetime"], "provider": meta["provider"],
                                   "bbox": bbox, "usable_fraction": cov["usable_fraction"],
                                   "nodata_fraction": cov["nodata_fraction"], "decision": cov["decision"],
                                   "licence_note": meta.get("licence_note"), "epsg": epsg}
        scenes.append({"name": name, "title": f"{name}: {meta['scene_id']} ({meta['datetime'][:10]})", "epsg": epsg,
                       "has_polygons": False, "has_mask_status": False})
    labels = out / "labels" / "mining_labels.gpkg"
    negs = out / "labels" / "hard_negatives.gpkg"
    if not labels.exists():
        write_label_template(labels)
    if not negs.exists():
        write_template(negs)
    neg_form = re.search(r"<fieldConfiguration>.*</defaults>", negs.with_suffix(".qml").read_text(), re.S).group(0)
    editable = [{"name": "Mining labels (digitise here)", "source": f"./labels/{labels.name}|layername={LABEL_LAYER}",
                 "epsg": 4326, "colour": (255, 0, 160), "form": label_form()},
                {"name": "Hard negatives (digitise here)", "source": f"./labels/{negs.name}|layername=hard_negatives",
                 "epsg": 4326, "colour": (0, 170, 255), "form": neg_form}]
    write_project(out, scenes, "Nigerian labelling kit", editable=editable)
    (out / "kit.json").write_text(json.dumps(report, indent=2))
    return report


# ---------- checking and locking ----------

def check_labels(path, kit_json=None) -> dict:
    """Named problems in the mining-label layer, and counts per site (FR-073)."""
    import geopandas as gpd
    from .negatives import EVIDENCE_GRADES, MIN_AREA_HA
    from .splits import _utm_epsg
    g = gpd.read_file(path, layer=LABEL_LAYER)
    problems = [f"missing field {f}" for f in LABEL_FIELDS if f not in g.columns]
    if problems:
        return {"features": int(len(g)), "problems": problems}
    def blank(col):
        return g[col].isna() | (g[col].astype(str).str.strip() == "")
    for f in ("site_id", "protocol_version", "scene_id", "image_date", "annotator"):
        if blank(f).any():
            problems.append(f"{int(blank(f).sum())} feature(s) without {f}")
    bad_ids = sorted(set(g.loc[~blank("site_id") & ~g["site_id"].astype(str).str.match(SITE_ID), "site_id"]))
    if bad_ids:
        problems.append(f"site_id must look like ng-<region>-001, got {bad_ids[:5]}")
    for col, allowed in (("label_class", LABEL_CLASSES), ("mine_scale", MINE_SCALES), ("evidence_grade", EVIDENCE_GRADES)):
        bad = sorted(set(g.loc[~g[col].isin(allowed), col].astype(str)))
        if bad:
            problems.append(f"{col} must be one of {list(allowed)}, got {bad[:5]}")
    if len(g):
        if (~g.geometry.is_valid).any():
            problems.append(f"{int((~g.geometry.is_valid).sum())} invalid geometries")
        c = g.geometry.representative_point().to_crs(4326)
        area = g.to_crs(_utm_epsg(float(c.x.median()), float(c.y.median()))).area / 1e4
        small = int(((area < MIN_AREA_HA) & (g["label_class"] == "mining_disturbance")).sum())
        if small:
            problems.append(f"{small} mining polygon(s) smaller than {MIN_AREA_HA} ha")
        if kit_json:
            kit = json.loads(Path(kit_json).read_text())["regions"]
            scenes = {v["scene_id"] for v in kit.values()}
            wrong = sorted(set(g["scene_id"].dropna()) - scenes)
            if wrong:
                problems.append(f"scene_id not in the kit (label on the kit's scenes): {wrong[:3]}")
        mixed = g.groupby("site_id")["scene_id"].nunique()
        if (mixed > 1).any():
            problems.append(f"sites labelled on more than one scene: {list(mixed[mixed > 1].index)[:5]}")
    mining = g[g["label_class"] == "mining_disturbance"]
    return {"features": int(len(g)), "sites": int(mining["site_id"].nunique()),
            "by_scale": {k: int(v) for k, v in mining.groupby("mine_scale")["site_id"].nunique().items()},
            "problems": problems}


def _site_hashes(g) -> dict:
    """Per-site SHA-256 over sorted geometries (WKB) and attributes, so any edit changes the hash."""
    out = {}
    for sid, part in g.groupby("site_id"):
        rows = sorted(json.dumps({k: (None if v != v else str(v)) for k, v in r.drop("geometry").items()}, sort_keys=True)
                      + r.geometry.normalize().wkb_hex for _, r in part.iterrows())
        out[str(sid)] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
    return out


def lock_labels(labels_path, out_path, kit_json=None, note="") -> dict:
    """Freeze all labelled Nigerian sites as the locked test set. Refuses problems and overwrites."""
    import geopandas as gpd
    out = Path(out_path)
    if out.exists():
        raise FileExistsError(f"{out} exists; a locked test set is never overwritten")
    rep = check_labels(labels_path, kit_json)
    if rep["problems"]:
        raise ValueError(f"fix the labels before locking: {rep['problems']}")
    if rep["sites"] == 0:
        raise ValueError("no mining sites to lock")
    g = gpd.read_file(labels_path, layer=LABEL_LAYER).to_crs(4326)
    hashes = _site_hashes(g)
    lock = {"purpose": "locked Nigerian test set (ML-07): test and threshold reporting only, never training",
            "site_ids": sorted(hashes), "count": len(hashes), "site_sha256": hashes,
            "sha256": hashlib.sha256("\n".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()).hexdigest(),
            "by_scale": rep["by_scale"], "locked_at": datetime.now(timezone.utc).isoformat(), "note": note}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, indent=2))
    return lock


def verify_lock(labels_path, lock_path) -> dict:
    """Which locked sites were edited, removed or added since locking. Empty lists when intact."""
    import geopandas as gpd
    lock = json.loads(Path(lock_path).read_text())
    now = _site_hashes(gpd.read_file(labels_path, layer=LABEL_LAYER).to_crs(4326))
    old = lock["site_sha256"]
    res = {"edited": sorted(k for k in old if k in now and now[k] != old[k]),
           "removed": sorted(set(old) - set(now)), "added": sorted(set(now) - set(old))}
    res["intact"] = not res["edited"] and not res["removed"]
    return res
