"""Detection job tests (FR-052, FR-080 to FR-085, ML-07 embargo) on synthetic imagery."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.contrib.gis.geos import Point
import rasterio
from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Organisation, User
from projects.models import Detection, Job, Project, Tile

from minefoot.preprocess import prepare_scene
from minefoot.synthetic import make_scene


def _area_of(prep):
    from rasterio.warp import transform_bounds
    with rasterio.open(Path(prep) / "reflectance.tif") as s:
        w, so, e, n = transform_bounds(s.crs, "EPSG:4326", *s.bounds)
    dx, dy = (e - w) * 0.05, (n - so) * 0.05
    return {"type": "Polygon", "coordinates": [[[w + dx, so + dy], [e - dx, so + dy], [e - dx, n - dy], [w + dx, n - dy], [w + dx, so + dy]]]}


class JobTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        raw = make_scene(cls.tmp / "raw", size=256, seed=7, scene_id="SYN")
        cls.good = cls.tmp / "good"; prepare_scene(raw, cls.good)
        cls.cloudy = cls.tmp / "cloudy"; shutil.copytree(cls.good, cls.cloudy)
        with rasterio.open(cls.cloudy / "scl.tif", "r+") as s:
            s.write(np.full((s.height, s.width), 9, "uint8"), 1)     # all cloud
        cls.truth = raw / "truth.geojson"
        cls.area = _area_of(cls.good)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.org = Organisation.objects.create(name="O")
        self.gis = User.objects.create_user("g", password="pw-long-enough", organisation=self.org, roles=["gis"])
        self.viewer = User.objects.create_user("v", password="pw-long-enough", organisation=self.org, roles=["viewer"])
        self.c = APIClient(); self.c.force_authenticate(self.gis)
        r = self.c.post("/api/projects/", {"name": "P", "purpose": "test", "acceptable_use": True}, format="json")
        self.project = Project.objects.get(pk=r.json()["id"])
        self.area_id = self.c.post(f"/api/projects/{self.project.pk}/areas/", {"geometry": self.area}, format="json").json()["id"]
        w, s, e, n = np.array(self.area["coordinates"][0]).min(0).tolist() + np.array(self.area["coordinates"][0]).max(0).tolist()
        self.item = {"id": "S2_SYN", "type": "Feature", "stac_version": "1.0.0", "links": [], "assets": {},
                     "bbox": [w - 1, s - 1, e + 1, n + 1],
                     "geometry": {"type": "Polygon", "coordinates": [[[w - 1, s - 1], [e + 1, s - 1], [e + 1, n + 1], [w - 1, n + 1], [w - 1, s - 1]]]},
                     "properties": {"datetime": "2026-01-03T10:00:00Z", "eo:cloud_cover": 0.5, "platform": "sentinel-2b",
                                    "s2:processing_baseline": "05.11"}}
        self.fetched = []
        self.cloudy_tile = None

        def fake_fetch(provider, item, bbox, out, name):
            self.fetched.append(name)
            if self.cloudy_tile is None:
                self.cloudy_tile = name                  # the first tile processed is fully clouded
            return self.cloudy if name == self.cloudy_tile else self.good

        def fake_score(prep, run_dir=None):
            import geopandas as gpd
            from minefoot.labels import rasterize_to
            lab = rasterize_to(Path(prep) / "reflectance.tif", gpd.read_file(self.truth)).astype(bool)
            return np.where(lab, 0.9, 0.05).astype("float32")

        card = {"model_version": "test:unet", "threshold": 0.5, "score_type": "model score", "tested_in_nigeria": False,
                "domain_warning": "Not yet tested on Nigerian sites (gate G3 pending)."}
        self.patches = [mock.patch("projects.pipeline.find_scenes", return_value=("planetary", [self.item])),
                        mock.patch("projects.pipeline.fetch_tile", side_effect=fake_fetch),
                        mock.patch("projects.pipeline.score_prepared", side_effect=fake_score),
                        mock.patch("projects.pipeline.model_card", return_value=card)]
        for p in self.patches:
            p.start()
        from minefoot_web.celery import app
        app.conf.task_always_eager = True   # run the Celery task inline in tests
        self.settings_patch = override_settings(MINEFOOT={**settings.MINEFOOT,
                                                          "TILE_KM": 1.0, "JOB_ROOT": self.tmp / "jobs",
                                                          "NIGERIA_TEST_LOCK": self.tmp / "no_lock.json"})
        self.settings_patch.enable()

    def tearDown(self):
        self.settings_patch.disable()
        for p in self.patches:
            p.stop()

    def estimate(self):
        r = self.c.post(f"/api/projects/{self.project.pk}/jobs/",
                        {"area": self.area_id, "start": "2026-01-01", "end": "2026-02-28", "max_cloud": 20}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def run_job(self, job_id):
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(f"/api/jobs/{job_id}/start/")
        self.assertEqual(r.status_code, 200, r.content)
        return Job.objects.get(pk=job_id)

    def test_estimate_then_run_gives_four_outcome_tiles_and_merged_candidates(self):
        est = self.estimate()
        self.assertEqual(est["state"], "estimated")
        self.assertGreater(est["estimate"]["tiles"], 1)
        self.assertIn("Nigerian", est["estimate"]["model"]["domain_warning"])
        job = self.run_job(est["id"])
        self.assertEqual(job.state, "succeeded_with_warnings", job.message)
        outcomes = {t.grid_id: t.outcome for t in job.tiles.all()}
        self.assertEqual(outcomes[self.cloudy_tile], "insufficient_usable_imagery")   # FR-052: never "no candidate"
        self.assertTrue(set(outcomes.values()) <= set(Tile.Outcome.values))
        self.assertIn("candidate_found", outcomes.values())
        dets = Detection.objects.filter(job=job)
        self.assertGreater(dets.count(), 0)
        tiles_hit = sum(o == "candidate_found" for o in outcomes.values())
        self.assertLess(dets.count(), sum(t.candidates for t in job.tiles.all()) + 1)   # pieces merged across tiles
        d = dets.first()
        self.assertEqual(d.score_type, "model score")
        self.assertIn(d.mask_status, ("assessed", "partly_masked", "mostly_masked"))
        self.assertTrue(d.scene.source_id == "S2_SYN" and tiles_hit >= 1)
        # FR-021/FR-023: checksums of fetched rasters, retrieval time, and FR-132 progress.
        import hashlib
        t0 = job.tiles.exclude(prepared_path="").first()
        self.assertEqual(t0.checksums["reflectance.tif"],
                         hashlib.sha256((Path(t0.prepared_path) / "reflectance.tif").read_bytes()).hexdigest())
        self.assertIsNotNone(t0.scene.retrieved_at)
        prog = self.c.get(f"/api/jobs/{job.pk}/").json()["progress"]
        self.assertEqual(prog["tiles_done"], job.tiles.count())
        self.assertGreater(prog["bytes_fetched"], 0)
        cov = prog["area_coverage"]   # FR-051: one tile fully clouded, so part of the area is not assessed
        self.assertAlmostEqual(cov["usable_pct"] + cov["not_assessed_pct"], 100, places=0)
        self.assertGreater(cov["not_assessed_pct"], 0)
        fc = self.c.get(f"/api/jobs/{job.pk}/tiles/").json()
        self.assertEqual(len(fc["features"]), job.tiles.count())
        self.assertEqual(fc["features"][0]["properties"]["date"], "2026-01-03")
        # FR-124: the score heatmap shows the job's stored score map (0.9 inside the truth, 0.05 outside).
        from io import BytesIO
        from PIL import Image
        with override_settings(MINEFOOT={**settings.MINEFOOT, "IMAGE_CACHE": self.tmp / "cache"}):
            self.assertEqual(self.c.get(f"/api/detections/{d.pk}/image/?layer=score&bounds=1").status_code, 200)
            px = np.asarray(Image.open(BytesIO(b"".join(self.c.get(f"/api/detections/{d.pk}/image/?layer=score").streaming_content))))
        from projects.imagery import score_rgba
        expect = score_rgba(np.array([[round(0.9 * 255)], [round(0.05 * 255)]], "uint8"))
        shown = px[px[..., 3] > 0]
        self.assertTrue(len(shown) and (np.abs(shown.astype(int) - expect[0, 0].astype(int)).max(axis=1) <= 1).all())
        self.assertEqual(expect[1, 0, 3], 0)   # 0.05 is under the 0.1 floor: transparent, so the imagery shows
        # FR-122: an optional per-view stretch changes the picture; bad values are refused with a reason.
        with override_settings(MINEFOOT={**settings.MINEFOOT, "IMAGE_CACHE": self.tmp / "cache"}):
            img = lambda q: np.asarray(Image.open(BytesIO(b"".join(self.c.get(f"/api/detections/{d.pk}/image/?layer=true_colour{q}").streaming_content))))
            fixed, tight = img(""), img("&stretch=0,0.1")
            self.assertGreater(tight[..., :3].mean(), fixed[..., :3].mean())   # a tighter range is brighter
            self.assertEqual(self.c.get(f"/api/detections/{d.pk}/image/?layer=ndvi&stretch=0,0.1").status_code, 400)
            self.assertEqual(self.c.get(f"/api/detections/{d.pk}/image/?layer=true_colour&stretch=0.5,0.2").status_code, 400)
        # FR-128: the pixel inspector matches the source rasters at 20 sampled points.
        import rasterio
        from rasterio.warp import transform as warp
        t = job.tiles.filter(outcome="candidate_found").first()
        rng = np.random.default_rng(0)
        with rasterio.open(Path(t.prepared_path) / "reflectance.tif") as src, rasterio.open(Path(t.prepared_path) / "scl.tif") as scl, \
                rasterio.open(t.score_path) as sc:
            refl, scl_a, sc_a = src.read(), scl.read(1), sc.read(1)
            x0, y0, x1, y1 = t.geometry.extent
            checked = 0
            for _ in range(500):
                lon, lat = rng.uniform(x0, x1), rng.uniform(y0, y1)
                if checked == 20 or not t.geometry.contains(Point(lon, lat, srid=4326)):
                    continue
                v = self.c.get(f"/api/detections/{d.pk}/pixel/?lon={lon}&lat={lat}").json()
                if v.get("tile") != t.grid_id:
                    continue   # overlapping tiles: the inspector read the other one
                (x,), (y,) = warp("EPSG:4326", src.crs, [lon], [lat])
                row, col = src.index(x, y)
                self.assertEqual((v["row"], v["col"]), (row, col))
                for i, b in enumerate(("B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12")):
                    want = float(refl[i, row, col])
                    if np.isfinite(want):
                        self.assertAlmostEqual(v["reflectance"][b], want, places=4)
                    else:
                        self.assertIsNone(v["reflectance"][b])
                self.assertEqual(v["scl"], int(scl_a[row, col]))
                self.assertAlmostEqual(v["model_score"], sc_a[row, col] / 255, places=3)
                checked += 1
        self.assertEqual(checked, 20)

    def test_threshold_preview_matches_job_and_saves_as_new_job(self):
        """FR-126: preview at the job's own settings reproduces the job; saving makes a new job and keeps the old."""
        job = self.run_job(self.estimate()["id"])
        n = Detection.objects.filter(job=job).count()
        self.assertGreater(n, 0)
        same = self.c.get(f"/api/jobs/{job.pk}/preview/").json()
        self.assertEqual(same["summary"], {**same["summary"], "candidates": n, "kept": n, "added": 0, "removed": 0})
        total = sum(d.area_ha for d in Detection.objects.filter(job=job))
        self.assertAlmostEqual(same["summary"]["area_ha"], total, delta=0.1)
        none = self.c.get(f"/api/jobs/{job.pk}/preview/?threshold=0.95&mmu_ha=1").json()   # fake scores are 0.9 at most
        self.assertEqual((none["summary"]["candidates"], none["summary"]["removed"]), (0, n))
        self.assertEqual(self.c.get(f"/api/jobs/{job.pk}/preview/?threshold=2").status_code, 400)
        big = self.c.get(f"/api/jobs/{job.pk}/preview/?threshold=0.5&mmu_ha=50").json()["summary"]["candidates"]
        self.assertLessEqual(big, n)
        r = self.c.post(f"/api/jobs/{job.pk}/rethreshold/", {"threshold": 0.5, "mmu_ha": 50}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        new = Job.objects.get(pk=r.json()["id"])
        self.assertEqual(new.parameters["derived_from"], job.pk)
        self.assertEqual(Detection.objects.filter(job=new).count(), big)
        self.assertEqual(Detection.objects.filter(job=job).count(), n)   # the original is untouched
        from projects.exports import selected_jobs
        self.assertEqual([j.pk for j in selected_jobs(self.project)], [new.pk])   # the area's latest result

    def test_compare_dates_lists_other_jobs_imagery(self):
        """FR-127: a candidate can be compared with another date's imagery of the same ground."""
        first = self.run_job(self.estimate()["id"])
        self.item["id"], self.item["properties"]["datetime"] = "S2_SYN_LATER", "2026-02-12T10:00:00Z"   # a later scene
        second = self.run_job(self.estimate()["id"])
        d = Detection.objects.filter(job=second).first()
        r = self.c.get(f"/api/detections/{d.pk}/dates/").json()
        self.assertEqual(r["current"]["date"], "2026-02-12")
        self.assertEqual([(o["job"], o["date"]) for o in r["others"]], [(first.pk, "2026-01-03")])
        with override_settings(MINEFOOT={**settings.MINEFOOT, "IMAGE_CACHE": self.tmp / "cache2"}):
            b = self.c.get(f"/api/detections/{d.pk}/image/?layer=true_colour&job={first.pk}&bounds=1").json()
            self.assertEqual((b["date"][:10], b["scene_id"]), ("2026-01-03", "S2_SYN"))
            self.assertEqual(self.c.get(f"/api/detections/{d.pk}/image/?layer=true_colour&job=999999").status_code, 404)

    def test_sources_must_be_approved_for_the_projects_use(self):
        """FR-025: a commercial project never searches Planetary Computer (evaluation-only terms); with no approved
        source the estimate is refused with the reason; an approval withdrawn after the estimate blocks the start."""
        from projects import pipeline
        from projects.models import SourceApproval
        Project.objects.filter(pk=self.project.pk).update(intended_use="commercial")
        with mock.patch("minefoot.catalog.cdse_keys", return_value=None):
            est = self.estimate()
            providers = pipeline.find_scenes.call_args.kwargs["providers"]
            self.assertEqual(providers, ["earth-search"])
            self.assertIn("planetary", est["estimate"]["sources_not_approved"])
            SourceApproval.objects.filter(provider="earth-search").update(approved=False)
            r = self.c.post(f"/api/jobs/{est['id']}/start/")
            self.assertEqual(r.status_code, 400)
            self.assertIn("no longer approved", r.json()["detail"])
            r = self.c.post(f"/api/projects/{self.project.pk}/jobs/",
                            {"area": self.area_id, "start": "2026-01-01", "end": "2026-02-28"}, format="json")
            self.assertEqual(r.status_code, 400)
            self.assertIn("evaluation and testing", r.json()["detail"])

    def test_cost_recorded_and_quotas_enforced(self):
        """FR-086: the job records measured time and storage against its estimate. FR-087: a job over quota is refused."""
        job = self.run_job(self.estimate()["id"])
        est, act = job.estimate["cost"], job.actual
        self.assertGreater(act["storage_bytes"], 0)
        self.assertEqual(act["tiles_processed"], job.tiles.exclude(prepared_path="").count())
        self.assertIsNotNone(act["seconds_vs_estimate"])
        self.assertIn("defaults", est["basis"])   # fewer than three measured jobs yet
        self.assertIsNone(act["money"])           # no rates configured
        with override_settings(MINEFOOT={**settings.MINEFOOT, "CPU_COST_PER_HOUR": 0.5, "STORAGE_COST_PER_GB_MONTH": 0.02,
                                         "JOB_ROOT": self.tmp / "jobs"}):
            from projects.costs import money
            self.assertEqual(money(3600, 2e9), {"compute": 0.5, "storage_per_month": 0.04, "currency": "USD"})
        Organisation.objects.filter(pk=self.org.pk).update(quota_jobs_per_month=1)
        est2 = self.estimate()
        self.assertFalse(est2["estimate"]["quota"]["ok"])
        r = self.c.post(f"/api/jobs/{est2['id']}/start/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("quota", r.json()["detail"])
        Organisation.objects.filter(pk=self.org.pk).update(quota_jobs_per_month=None, quota_km2_per_month=0.001)
        r = self.c.post(f"/api/jobs/{est2['id']}/start/")
        self.assertIn("monthly quota", r.json()["detail"])

    def test_parameters_frozen_and_cancel_resume_skips_done_tiles(self):
        est = self.estimate()
        job = Job.objects.get(pk=est["id"])
        # Start, then cancel before the worker runs: the job stops before its first tile.
        with mock.patch("projects.tasks.run_detection_job.delay"):
            self.c.post(f"/api/jobs/{job.pk}/start/")
        self.assertEqual(self.c.post(f"/api/jobs/{job.pk}/start/").status_code, 400)   # cannot start twice
        self.c.post(f"/api/jobs/{job.pk}/cancel/")
        from projects.tasks import run_detection_job
        Job.objects.filter(pk=job.pk).update(state="cancelling")
        run_detection_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.state, "cancelled")
        # Mark one tile done by hand, then resume: that tile is not fetched again.
        done = job.tiles.order_by("grid_id").first()
        Tile.objects.filter(pk=done.pk).update(state="done", outcome="no_candidate_found_in_usable_imagery")
        self.fetched.clear()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.c.post(f"/api/jobs/{job.pk}/resume/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn(done.grid_id, self.fetched)
        self.assertEqual(len(self.fetched), job.tiles.count() - 1)
        self.assertIn(Job.objects.get(pk=job.pk).state, ("succeeded", "succeeded_with_warnings"))

    def test_embargo_on_nigerian_regions_until_locked(self):
        jos = {"type": "Polygon", "coordinates": [[[8.9, 9.6], [8.95, 9.6], [8.95, 9.65], [8.9, 9.65], [8.9, 9.6]]]}
        a = self.c.post(f"/api/projects/{self.project.pk}/areas/", {"geometry": jos}, format="json").json()["id"]
        body = {"area": a, "start": "2026-01-01", "end": "2026-02-28"}
        r = self.c.post(f"/api/projects/{self.project.pk}/jobs/", body, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Nigerian test region", r.json()["detail"])
        lock = self.tmp / "lock.json"; lock.write_text("{}")
        with override_settings(MINEFOOT={**settings.MINEFOOT, "NIGERIA_TEST_LOCK": lock}):
            self.assertEqual(self.c.post(f"/api/projects/{self.project.pk}/jobs/", body, format="json").status_code, 201)

    def test_only_gis_runs_jobs(self):
        v = APIClient(); v.force_authenticate(self.viewer)
        r = v.post(f"/api/projects/{self.project.pk}/jobs/", {"area": self.area_id, "start": "2026-01-01", "end": "2026-02-28"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(v.get(f"/api/projects/{self.project.pk}/jobs/").status_code, 403)
