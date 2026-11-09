"""Detection job tests (FR-052, FR-080 to FR-085, ML-07 embargo) on synthetic imagery."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
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
        fc = self.c.get(f"/api/jobs/{job.pk}/tiles/").json()
        self.assertEqual(len(fc["features"]), job.tiles.count())
        self.assertEqual(fc["features"][0]["properties"]["date"], "2026-01-03")

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
