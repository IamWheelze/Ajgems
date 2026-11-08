"""Acceptance tests for the web app's rules (FRD 7.1, 7.2, 7.10, 9)."""
import io
import json
import zipfile
from datetime import datetime, timezone

from django.contrib.gis.geos import Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Organisation, User
from audit.models import AuditEvent
from projects.models import Detection, Job, Project, Scene
from review.models import Review

ROLES = ("admin", "ml", "gis", "field", "viewer")
SQUARE = {"type": "Polygon", "coordinates": [[[8.8, 9.7], [8.9, 9.7], [8.9, 9.8], [8.8, 9.8], [8.8, 9.7]]]}


def reference_km2(geojson):
    """Independent reference: area in a Lambert equal-area projection centred on the polygon."""
    import geopandas as gpd
    from shapely.geometry import shape
    g = shape(geojson); c = g.centroid
    laea = f"+proj=laea +lat_0={c.y} +lon_0={c.x} +datum=WGS84 +units=m"
    return float(gpd.GeoSeries([g], crs=4326).to_crs(laea).area.iloc[0] / 1e6)


def make_world():
    orgs = {n: Organisation.objects.create(name=n) for n in ("A", "B")}
    users = {}
    for o in orgs.values():
        for r in ROLES:
            users[(o.name, r)] = User.objects.create_user(f"{o.name}_{r}", password="pw-long-enough", organisation=o,
                                                          roles=[r])
    scene = Scene.objects.create(source_id="S2_TEST", provider="test", sensor="sentinel-2b",
                                 acquired_at=datetime(2026, 1, 3, tzinfo=timezone.utc), processing_baseline="05.11",
                                 cloud_pct=0.1, licence_note="Contains modified Copernicus Sentinel data 2026")
    projects, dets = {}, {}
    for o in orgs.values():
        gis = users[(o.name, "gis")]
        p = Project.objects.create(organisation=o, name=f"P{o.name}", purpose="test", created_by=gis,
                                   acceptable_use_accepted_by=gis, acceptable_use_accepted_at=datetime.now(timezone.utc))
        job = Job.objects.create(organisation=o, project=p, model_version="g2_hn", created_by=gis)
        poly = Polygon.from_bbox((8.80, 9.70, 8.81, 9.71)); poly.srid = 4326
        d = Detection.objects.create(organisation=o, project=p, job=job, scene=scene, geometry=poly,
                                     original_geometry=poly, area_ha=120.0, score_mean=0.8, score_band="high",
                                     threshold=0.41, min_mapping_unit_ha=1.0, model_version="g2_hn",
                                     input_schema_version="s2-l2a-10band-v1", masked_fraction=0.0,
                                     mask_status="assessed")
        projects[o.name], dets[o.name] = p, d
    return orgs, users, projects, dets


class Base(TestCase):
    def setUp(self):
        self.orgs, self.users, self.projects, self.dets = make_world()

    def client_for(self, org, role):
        c = APIClient()
        c.force_authenticate(self.users[(org, role)])
        return c


class PermissionMatrixTest(Base):
    """FR-002: every role against every endpoint, with the expected outcome."""

    def test_matrix(self):
        p, d = self.projects["A"], self.dets["A"]
        endpoints = [
            ("get", "/api/projects/", None, set(ROLES)),
            ("post", "/api/projects/", {"name": "new", "purpose": "x", "acceptable_use": True}, {"gis"}),
            ("patch", f"/api/projects/{p.pk}/", {"purpose": "changed"}, {"gis"}),
            ("post", f"/api/projects/{p.pk}/archive/", {}, {"gis", "admin"}),
            ("get", f"/api/projects/{p.pk}/areas/", None, {"admin", "gis", "field", "viewer"}),
            ("post", f"/api/projects/{p.pk}/areas/", {"geometry": SQUARE}, {"gis"}),
            ("get", f"/api/projects/{p.pk}/detections/", None, {"admin", "gis", "field", "viewer"}),
            ("get", "/api/review-options/", None, {"admin", "gis", "field", "viewer"}),
            ("get", "/api/audit/", None, {"admin"}),
        ]
        for method, url, body, allowed in endpoints:
            for role in ROLES:
                if method == "post" and url.endswith("/archive/"):
                    self.projects["A"].status = "active"; self.projects["A"].save()
                r = getattr(self.client_for("A", role), method)(url, body, format="json")
                ok = r.status_code < 400
                self.assertEqual(ok, role in allowed, f"{method} {url} as {role}: {r.status_code} {r.content[:120]}")
                if method == "post" and url == "/api/projects/" and ok:
                    Project.objects.filter(name="new").delete()

    def test_unauthenticated_refused(self):
        self.assertIn(APIClient().get("/api/projects/").status_code, (401, 403))


class IsolationTest(Base):
    """FR-003: a cross-organisation request by ID returns not found."""

    def test_cross_org_not_found(self):
        pb, db = self.projects["B"], self.dets["B"]
        c = self.client_for("A", "gis")
        for url in (f"/api/projects/{pb.pk}/", f"/api/projects/{pb.pk}/areas/", f"/api/projects/{pb.pk}/detections/",
                    f"/api/detections/{db.pk}/", f"/api/scenes/{db.scene_id}/image/?bounds=1"):
            if url.startswith("/api/scenes/"):
                continue   # the scene is shared by both orgs in this fixture; covered by test_scene_not_used_by_org
            self.assertEqual(c.get(url).status_code, 404, url)
        self.assertEqual(c.post(f"/api/detections/{db.pk}/reviews/",
                                {"action": "confirm", "reason_code": "visible_disturbance"}, format="json").status_code, 404)
        self.assertEqual([x["id"] for x in c.get("/api/projects/").json()["results"]], [self.projects["A"].pk])

    def test_scene_not_used_by_org(self):
        other = Scene.objects.create(source_id="S2_OTHER", provider="test", sensor="s2",
                                     acquired_at=datetime(2026, 1, 3, tzinfo=timezone.utc), processing_baseline="05.11",
                                     licence_note="x")
        self.assertEqual(self.client_for("A", "gis").get(f"/api/scenes/{other.pk}/image/?bounds=1").status_code, 404)


class ReviewTest(Base):
    def post(self, role, body, det=None):
        det = det or self.dets["A"]
        return self.client_for("A", role).post(f"/api/detections/{det.pk}/reviews/", body, format="json")

    def test_reason_required_and_codes_checked(self):
        self.assertEqual(self.post("gis", {"action": "confirm"}).status_code, 400)
        self.assertEqual(self.post("gis", {"action": "confirm", "reason_code": "looks_like_a_tin_mine"}).status_code, 400)
        self.assertEqual(self.post("gis", {"action": "reject", "reason_code": "other"}).status_code, 400)
        self.assertEqual(self.post("gis", {"action": "reject", "reason_code": "other", "note": "a road"}).status_code, 201)

    def test_roles_and_transitions(self):
        self.assertEqual(self.post("viewer", {"action": "confirm", "reason_code": "visible_disturbance"}).status_code, 403)
        self.assertEqual(self.post("field", {"action": "field_confirm", "reason_code": "observed_on_site"}).status_code, 400)
        self.assertEqual(self.post("gis", {"action": "release", "reason_code": "review_complete"}).status_code, 400)
        self.assertEqual(self.post("gis", {"action": "request_field_check", "reason_code": "needs_ground_truth"}).status_code, 201)
        self.assertEqual(self.post("field", {"action": "field_confirm", "reason_code": "observed_on_site"}).status_code, 201)
        r = self.post("gis", {"action": "release", "reason_code": "review_complete"})
        self.assertEqual(r.status_code, 201)
        props = r.json()["properties"]
        self.assertEqual(props["status"], "released")
        self.assertEqual(props["evidence_grade"], "E3")
        self.assertEqual([x["action"] for x in props["reviews"]], ["request_field_check", "field_confirm", "release"])

    def test_edit_keeps_both_geometries(self):
        new = {"type": "Polygon", "coordinates": [[[8.80, 9.70], [8.805, 9.70], [8.805, 9.705], [8.80, 9.705], [8.80, 9.70]]]}
        self.assertEqual(self.post("gis", {"action": "edit", "reason_code": "boundary_too_large"}).status_code, 400)
        r = self.post("gis", {"action": "edit", "reason_code": "boundary_too_large", "geometry": new})
        self.assertEqual(r.status_code, 201)
        f = r.json()
        self.assertEqual(f["geometry"]["coordinates"][0][1][0], 8.805)
        self.assertAlmostEqual(f["properties"]["original_geometry"]["coordinates"][0][2][0], 8.81)
        self.assertIsNotNone(f["properties"]["reviews"][0]["geometry_before"])

    def test_history_is_append_only(self):
        self.post("gis", {"action": "confirm", "reason_code": "visible_disturbance"})
        rv = Review.objects.get()
        rv.note = "changed later"
        with self.assertRaises(PermissionError):
            rv.save()
        with self.assertRaises(PermissionError):
            rv.delete()
        for table in ("review_review", "audit_auditevent"):   # even raw SQL is refused by the trigger
            with self.assertRaises(Exception), connection.cursor() as cur:
                from django.db import transaction
                with transaction.atomic():
                    cur.execute(f"UPDATE {table} SET id = id")
            with self.assertRaises(Exception), connection.cursor() as cur:
                from django.db import transaction
                with transaction.atomic():
                    cur.execute(f"DELETE FROM {table}")
        self.assertTrue(AuditEvent.objects.filter(action="review.confirm").exists())

    def test_viewer_sees_released_only(self):
        v = self.client_for("A", "viewer")
        self.assertEqual(v.get(f"/api/projects/{self.projects['A'].pk}/detections/").json()["features"], [])
        self.assertEqual(v.get(f"/api/detections/{self.dets['A'].pk}/").status_code, 404)
        self.post("gis", {"action": "confirm", "reason_code": "visible_disturbance"})
        self.post("gis", {"action": "release", "reason_code": "review_complete"})
        self.assertEqual(len(v.get(f"/api/projects/{self.projects['A'].pk}/detections/").json()["features"]), 1)

    def test_payload_has_score_band_not_raw_score(self):
        f = self.client_for("A", "gis").get(f"/api/detections/{self.dets['A'].pk}/").json()["properties"]
        self.assertNotIn("score_mean", f)
        self.assertEqual(f["score_type"], "model score")
        self.assertIn("does not establish mineral type", f["statement"])


class AreaTest(Base):
    def post(self, body=None, file=None):
        c = self.client_for("A", "gis")
        url = f"/api/projects/{self.projects['A'].pk}/areas/"
        return c.post(url, {"file": file} if file else body, format="multipart" if file else "json")

    def test_drawn_area_and_geodesic_size(self):
        r = self.post({"geometry": SQUARE, "name": "Jos"})
        self.assertEqual(r.status_code, 201)
        ref = reference_km2(SQUARE)   # FR-011: within 0.1% of a reference
        self.assertLess(abs(r.json()["area_km2"] - ref) / ref, 0.001)

    def test_self_intersection_refused_with_reason(self):
        bow = {"type": "Polygon", "coordinates": [[[8.8, 9.7], [8.9, 9.8], [8.9, 9.7], [8.8, 9.8], [8.8, 9.7]]]}
        r = self.post({"geometry": bow})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Self-intersection", r.json()["detail"])

    def test_size_limit(self):
        big = {"type": "Polygon", "coordinates": [[[5, 7], [7, 7], [7, 9], [5, 9], [5, 7]]]}
        r = self.post({"geometry": big})
        self.assertEqual(r.status_code, 400)
        self.assertIn("limit", r.json()["detail"])

    def test_uploads(self):
        import geopandas as gpd
        from shapely.geometry import shape
        import tempfile, os
        g = gpd.GeoDataFrame({"n": [1]}, geometry=[shape(SQUARE)], crs=4326).to_crs(32632)
        with tempfile.TemporaryDirectory() as tmp:
            gpkg = os.path.join(tmp, "a.gpkg"); g.to_file(gpkg, driver="GPKG")
            r = self.post(file=SimpleUploadedFile("a.gpkg", open(gpkg, "rb").read()))
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(r.json()["source_crs"], "EPSG:32632")
            ref = reference_km2(SQUARE)
            self.assertLess(abs(r.json()["area_km2"] - ref) / ref, 0.001)
            # A zipped shapefile without .prj has no CRS: refused with that reason.
            g.to_file(os.path.join(tmp, "s.shp"))
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for ext in ("shp", "shx", "dbf"):
                    z.write(os.path.join(tmp, f"s.{ext}"), f"s.{ext}")
            r = self.post(file=SimpleUploadedFile("s.zip", buf.getvalue()))
            self.assertEqual(r.status_code, 400)
            self.assertIn(".prj", r.json()["detail"])

    def test_archived_project_read_only(self):
        self.client_for("A", "gis").post(f"/api/projects/{self.projects['A'].pk}/archive/")
        self.assertEqual(self.post({"geometry": SQUARE}).status_code, 400)
        r = self.client_for("A", "gis").patch(f"/api/projects/{self.projects['A'].pk}/", {"purpose": "x"}, format="json")
        self.assertEqual(r.status_code, 400)


class AuthAuditTest(Base):
    def test_login_and_failed_login_are_audited(self):
        c = APIClient()
        self.assertEqual(c.post("/api/auth/login/", {"username": "A_gis", "password": "wrong"}, format="json").status_code, 400)
        r = c.post("/api/auth/login/", {"username": "A_gis", "password": "pw-long-enough"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["roles"], ["gis"])
        self.assertTrue(AuditEvent.objects.filter(action="auth.login").exists())
        self.assertTrue(AuditEvent.objects.filter(action="auth.login_failed").exists())

    def test_project_needs_acceptable_use_and_purpose(self):
        c = self.client_for("A", "gis")
        self.assertEqual(c.post("/api/projects/", {"name": "n", "purpose": "x"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/projects/", {"name": "n", "purpose": " ", "acceptable_use": True},
                                format="json").status_code, 400)
        r = c.post("/api/projects/", {"name": "n", "purpose": "pilot", "acceptable_use": True}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(AuditEvent.objects.filter(action="project.create").exists())
