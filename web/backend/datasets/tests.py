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
