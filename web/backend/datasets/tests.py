"""FR-131: dataset explorer API, roles, filters and overlap warnings."""
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Organisation, User
from datasets.models import DatasetVersion, Overlap, Sample


class ExplorerTest(TestCase):
    def setUp(self):
        org = Organisation.objects.create(name="O")
        self.ml = User.objects.create_user("ml", password="pw-long-enough", organisation=org, roles=["ml"], mfa_enabled=True)
        self.gis = User.objects.create_user("gis", password="pw-long-enough", organisation=org, roles=["gis"])
        self.d = DatasetVersion.objects.create(name="ds", version="1", licence="CC BY 4.0", licence_status="ok", source="x",
                                               split_name="v1", split_sha256="a" * 64)
        for i, (split, scale) in enumerate([("train", "Artisanal"), ("train", "Industrial"), ("test", "Artisanal")]):
            p = Polygon.from_bbox((i, 0, i + 0.1, 0.1)); p.srid = 4326
            Sample.objects.create(dataset=self.d, site_id=f"s{i}", split=split, mine_scale=scale, mine_type="Surface",
                                  region="africa", footprint=p, labels=MultiPolygon(p), locked_test=split == "test")
        Overlap.objects.create(dataset=self.d, site_a="s0", site_b="s2", kind="footprint_overlap", cross_split=True, iou=0.2)

    def signed_in(self, u):
        c = APIClient(); c.force_login(u)
        if u.is_privileged:
            s = c.session; s["mfa_verified"] = True; s.save()
        return c

    def test_explorer(self):
        c = self.signed_in(self.ml)
        ds = c.get("/api/datasets/").json()[0]
        self.assertEqual(ds["overlaps"]["cross_split"], 1)                       # warning surfaced
        r = c.get(f"/api/datasets/{self.d.pk}/samples/?split=train").json()
        self.assertEqual(len(r["features"]), 2)
        self.assertEqual(r["balance"]["train"]["mine_scale"], {"Artisanal": 1, "Industrial": 1})
        sid = r["features"][0]["id"]
        det = c.get(f"/api/samples/{sid}/").json()
        self.assertIsNotNone(det["labels"]); self.assertEqual(det["overlaps"][0]["other"], "s2")
        self.assertEqual(self.signed_in(self.gis).get("/api/datasets/").status_code, 403)   # internal: ML and admin only


class DomainTest(TestCase):
    """FR-014: an area far from every test site of the model, or in an untested season, is flagged before the run."""

    def test_domain_flags(self):
        import json
        import tempfile
        from datetime import date
        from pathlib import Path
        from django.contrib.gis.geos import Polygon
        from projects.pipeline import domain_check
        d = DatasetVersion.objects.create(name="ds", version="1", licence="CC BY 4.0", licence_status="ok", source="x",
                                          split_name="v1", split_sha256="a" * 64)
        for i, (lon, month, split) in enumerate([(0.0, 1, "test"), (1.0, 7, "test"), (0.5, 3, "train")]):
            p = Polygon.from_bbox((lon, 6.0, lon + 0.05, 6.05)); p.srid = 4326
            Sample.objects.create(dataset=d, site_id=f"t{i}", split=split, mine_scale="Artisanal", mine_type="Surface",
                                  region="africa", footprint=p, labels=MultiPolygon(p), acquired=date(2024, month, 10))
        run = Path(tempfile.mkdtemp())
        (run / "config.json").write_text(json.dumps({"sites": {"test": ["t0", "t1"]}}))
        area = Polygon.from_bbox((0.2, 6.2, 0.3, 6.3)); area.srid = 4326
        r = domain_check(area, "2026-01-01", "2026-02-28", run_dir=run)
        self.assertTrue(r["inside"], r)
        self.assertEqual((r["test_sites"], r["nearest_test_site"], r["same_season_test_sites"]), (2, "t0", 1))
        self.assertLess(r["nearest_test_km"], 40)
        r = domain_check(area, "2026-04-01", "2026-05-31", run_dir=run)   # no test scene in Apr-May nearby
        self.assertFalse(r["inside"]); self.assertIn("Season not tested", r["flags"][0])
        far = Polygon.from_bbox((20.0, 6.0, 20.1, 6.1)); far.srid = 4326          # about 2,100 km east
        r = domain_check(far, run_dir=run)
        self.assertFalse(r["inside"]); self.assertIn("Outside the validated domain", r["flags"][0])
        self.assertGreater(r["nearest_test_km"], 2000)


class NegativeCountsTest(TestCase):
    """FR-074: hard-negative counts per type are recorded and reported for each dataset version."""

    def test_import_records_negatives_per_type(self):
        import tempfile
        from pathlib import Path
        import geopandas as gpd
        from django.core.management import call_command
        from shapely.geometry import box
        from accounts.models import Organisation, User
        from rest_framework.test import APIClient
        tmp = Path(tempfile.mkdtemp())
        sites = gpd.GeoDataFrame({"site_id": ["a", "b", "c"], "split": ["train", "val", "test"], "source": ["t"] * 3,
                                  "mine_scale": ["Artisanal"] * 3, "mine_type": ["Surface"] * 3},
                                 geometry=[box(i, 6, i + 0.05, 6.05) for i in (0, 2, 4)], crs=4326)
        sites.to_file(tmp / "split.gpkg", layer="sites", driver="GPKG")
        neg = gpd.GeoDataFrame({"neg_type": ["farmland", "farmland", "riverbank"], "protocol_version": ["0.1"] * 3,
                                "scene_id": ["S"] * 3, "image_date": ["2026-01-01"] * 3, "annotator": ["x"] * 3,
                                "evidence_grade": ["E1"] * 3, "notes": [""] * 3},
                               geometry=[box(0, 6, 0.01, 6.01), box(0.02, 6, 0.03, 6.01), box(2, 6, 2.01, 6.01)], crs=4326)
        neg.to_file(tmp / "neg.gpkg", layer="hard_negatives", driver="GPKG")
        call_command("import_dataset", split=str(tmp / "split.gpkg"), name="t_ds", dataset_version="1", negatives=str(tmp / "neg.gpkg"))
        call_command("import_dataset", split=str(tmp / "split.gpkg"), name="t_ds", dataset_version="0")
        org = Organisation.objects.create(name="O")
        u = User.objects.create_user("ml", password="pw-long-enough", organisation=org, roles=["ml"], mfa_enabled=True)
        c = APIClient(); c.force_login(u); s = c.session; s["mfa_verified"] = True; s.save()
        rows = {d["version"]: d["negatives"] for d in c.get("/api/datasets/").json()}
        self.assertEqual((rows["1"]["by_type"]["farmland"], rows["1"]["by_type"]["riverbank"], rows["1"]["features"]), (2, 1, 3))
        self.assertIn("no digitised hard negatives", rows["0"]["note"])
