"""Load an internal demo from a finished minefoot run: an organisation, one user per role,
a project, and candidate polygons vectorised from the run's test-site score maps.

These are Global ML-ready test sites (internal research), not Nigerian results.

    python manage.py load_demo --run ../../data/runs/g2_hn --password 'choose-a-password' --sites 20
"""
import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Organisation, User
from audit.models import record
from projects.models import Detection, Job, Project, Scene

ORG = "Internal research (demo)"
PROJECT = "Global ML-ready test sites (internal research)"


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p):
        p.add_argument("--run", required=True, help="minefoot G2 run folder (metrics.json, scores/)")
        p.add_argument("--model", default="unet")
        p.add_argument("--prepared-root", default=None)
        p.add_argument("--password", required=True, help="password for the demo users (10+ characters)")
        p.add_argument("--sites", type=int, default=20)
        p.add_argument("--mmu-ha", type=float, default=1.0)

    def handle(self, run, model, prepared_root, password, sites, mmu_ha, **_):
        from minefoot.vectorise import vectorise
        run = Path(run).resolve()
        prepared_root = Path(prepared_root or settings.MINEFOOT["DATA_ROOT"] / "prepared" / "global_ml_ready").resolve()
        metrics = json.loads((run / "metrics.json").read_text())["models"][model]
        cfg = json.loads((run / "config.json").read_text())
        threshold = metrics["threshold_score"]
        site_ids = [r["site_id"] for r in metrics["test"]["per_site"]][:sites]
        if len(password) < 10:
            raise CommandError("use a password of at least 10 characters")
        with transaction.atomic():
            org, _ = Organisation.objects.get_or_create(name=ORG, defaults={"region": "global (internal research)"})
            users = {}
            for role in ("admin", "gis", "field", "viewer", "ml"):
                u, created = User.objects.get_or_create(username=f"demo_{role}", defaults={"organisation": org, "roles": [role]})
                u.organisation, u.roles = org, [role]
                u.set_password(password)
                u.save()
                users[role] = u
            gis = users["gis"]
            if Project.objects.filter(organisation=org, name=PROJECT).exists():
                raise CommandError(f"'{PROJECT}' already exists; delete the demo database to reload")
            project = Project.objects.create(
                organisation=org, name=PROJECT, created_by=gis, acceptable_use_accepted_by=gis,
                acceptable_use_accepted_at=timezone.now(),
                purpose=f"Internal demo of the review workspace using model run {run.name} on Global ML-ready test "
                        f"sites. Not Nigerian results and not for release.")
            job = Job.objects.create(organisation=org, project=project, kind="detection", state=Job.State.SUCCEEDED,
                                     model_version=f"{run.name}:{model}", created_by=gis, started_at=timezone.now(),
                                     finished_at=timezone.now(),
                                     parameters={"threshold": threshold, "mmu_ha": mmu_ha, "code_commit": cfg.get("code_commit"),
                                                 "source": "load_demo from stored score maps"})
            n = 0
            for sid in site_ids:
                prep = prepared_root / sid
                meta = json.loads((prep / "prepared.json").read_text())
                scene, _ = Scene.objects.get_or_create(
                    provider=meta["provider"], source_id=meta["scene_id"],
                    defaults=dict(sensor=meta.get("sensor") or "", processing_baseline=meta["processing_baseline"],
                                  acquired_at=datetime.fromisoformat(meta["datetime"].replace("Z", "+00:00")),
                                  cloud_pct=meta.get("cloud_pct_scene"), licence_note=meta.get("licence_note", ""),
                                  footprint=Polygon.from_bbox(meta["bbox_wgs84"]), prepared_path=str(prep)))
                job.scenes.add(scene)
                g = vectorise(run / "scores" / f"{sid}__{model}.tif", threshold, mmu_ha, prepared_dir=prep)
                for _, r in g.iterrows():
                    geom = GEOSGeometry(r.geometry.wkt, srid=4326)
                    if geom.geom_type != "Polygon":
                        continue
                    Detection.objects.create(
                        organisation=org, project=project, job=job, scene=scene, geometry=geom, original_geometry=geom,
                        area_ha=float(r.area_ha), score_mean=float(r.score_mean), score_band=r.score_band,
                        threshold=threshold, min_mapping_unit_ha=mmu_ha, model_version=job.model_version,
                        input_schema_version=meta.get("input_schema", "s2-l2a-10band-v1"),
                        masked_fraction=float(r.masked_fraction), mask_status=r.mask_status)
                    n += 1
                self.stdout.write(f"{sid}: {len(g)} candidates")
            record(gis, "demo.load", project, after={"run": str(run), "sites": len(site_ids), "detections": n})
        self.stdout.write(self.style.SUCCESS(f"loaded {n} candidates on {len(site_ids)} sites; users demo_admin, "
                                             f"demo_gis, demo_field, demo_viewer, demo_ml"))
