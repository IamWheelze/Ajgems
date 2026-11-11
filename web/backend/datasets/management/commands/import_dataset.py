"""Import a dataset version's sites, split, labels and overlaps for the dataset explorer (FR-131).

    python manage.py import_dataset --split ../../data/splits/gmlr_split.gpkg \
        --overlaps ../../data/splits/gmlr_split_overlaps.csv \
        --lock ../../docs/evidence/test_lock_global_ml_ready_v1.json --name global_ml_ready --dataset-version 1.0.0
"""
import csv
import hashlib
import json
from pathlib import Path

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand
from django.db import transaction

from datasets.models import DatasetVersion, Overlap, Sample


def licence_status(name):
    reg = Path(__file__).resolve().parents[5] / "docs" / "licence_register.csv"
    key = {"global_ml_ready": "Global ML-ready"}.get(name, name)
    if reg.exists():
        for row in csv.DictReader(open(reg)):
            if row["item"].startswith(key):
                return row["licence_seen"][:60], row["commercial_status"][:200]
    return "unknown", "NOT IN LICENCE REGISTER: do not train on it"


def _negatives(path):
    """Counts per type and problems for a digitised hard-negative layer, or a note that none was given."""
    if not path:
        return {"by_type": {}, "features": 0, "note": "no digitised hard negatives recorded for this version"}
    from minefoot.negatives import check_negatives
    r = check_negatives(path)
    return {**r, "source": Path(path).name, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p):
        for a in ("--split", "--name", "--dataset-version"):
            p.add_argument(a, required=True)
        p.add_argument("--overlaps"); p.add_argument("--lock"); p.add_argument("--source", default="")
        p.add_argument("--negatives", help="digitised hard_negatives.gpkg for this version (FR-074)")

    def handle(self, split, name, dataset_version, overlaps, lock, source, negatives=None, **_):
        version = dataset_version
        import geopandas as gpd
        from minefoot.datasets import _region
        from minefoot.splits import check_split
        sites = gpd.read_file(split, layer="sites").to_crs(4326)
        try:
            labels = gpd.read_file(split, layer="labels").to_crs(4326).dissolve(by="site_id")
        except Exception:
            labels = None
        locked = set(json.loads(Path(lock).read_text())["site_ids"]) if lock else set()
        lic, status = licence_status(name)
        check = check_split(sites, buffer_km=10)
        c = sites.to_crs(3857).geometry.centroid.to_crs(4326)
        with transaction.atomic():
            DatasetVersion.objects.filter(name=name, version=version, split_name=Path(split).stem).delete()
            dv = DatasetVersion.objects.create(
                name=name, version=version, licence=lic, licence_status=status, source=source or str(sites["source"].iloc[0]),
                state=DatasetVersion.State.LICENCE_REVIEW if "review" in status.lower() else DatasetVersion.State.INGESTED,
                split_name=Path(split).stem, split_sha256=hashlib.sha256(Path(split).read_bytes()).hexdigest(),
                test_lock_sha256=json.loads(Path(lock).read_text())["sha256"] if lock else "",
                split_check={k: (v if isinstance(v, bool) else len(v)) for k, v in check.items()},
                negatives=_negatives(negatives))
            objs = []
            for (_, r), x, y in zip(sites.iterrows(), c.x, c.y):
                lab = None
                if labels is not None and r["site_id"] in labels.index:
                    g = GEOSGeometry(labels.loc[r["site_id"]].geometry.wkt, srid=4326)
                    lab = g if g.geom_type == "MultiPolygon" else MultiPolygon(g) if g.geom_type == "Polygon" else None
                fp = GEOSGeometry(r.geometry.wkt, srid=4326)
                objs.append(Sample(dataset=dv, site_id=r["site_id"], split=r["split"], source_split=r.get("source_split") or "",
                                   unit_id=r.get("unit_id") or "", block_id=r.get("block_id") or "",
                                   mine_type=r.get("mine_type") or "", mine_scale=r.get("mine_scale") or "",
                                   region=_region(x, y), scene_id=r.get("scene_id") or "", acquired=r.get("acquired") or None,
                                   footprint=fp if fp.geom_type == "Polygon" else fp[0], labels=lab,
                                   locked_test=r["site_id"] in locked))
            Sample.objects.bulk_create(objs, batch_size=500)
            n_ov = 0
            if overlaps:
                split_of = dict(zip(sites["site_id"], sites["split"]))
                rows = list(csv.DictReader(open(overlaps)))
                Overlap.objects.bulk_create([Overlap(dataset=dv, site_a=o["site_a"], site_b=o["site_b"], kind=o["kind"],
                                                     cross_split=split_of.get(o["site_a"]) != split_of.get(o["site_b"]),
                                                     iou=float(o["iou"]) if o.get("iou") else None) for o in rows], batch_size=1000)
                n_ov = len(rows)
        self.stdout.write(self.style.SUCCESS(f"{name} {version}: {len(objs)} samples, {n_ov} overlap pairs, "
                                             f"{len(locked)} locked test sites, split check ok={check['ok']}"))
