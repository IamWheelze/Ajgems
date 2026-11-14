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
    """Two organisations; each has one project, one finished job and one detection."""
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
        job = Job.objects.create(organisation=o, project=p, model_version="g2_hn", created_by=gis, state="succeeded")
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
        """A signed-in client. Privileged users have passed their second factor (FR-001)."""
        u = self.users[(org, role)]
        c = APIClient()
        c.force_login(u)
        if u.is_privileged:
            User.objects.filter(pk=u.pk).update(mfa_enabled=True)
            session = c.session
            session["mfa_verified"] = True
            session.save()
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
        """FR-010: an archived project is read-only and keeps its history."""
        c = self.client_for("A", "gis")
        self.assertEqual(self.post({"geometry": SQUARE}).status_code, 201)
        c.post(f"/api/projects/{self.projects['A'].pk}/archive/")
        self.assertEqual(self.post({"geometry": SQUARE}).status_code, 400)
        r = c.patch(f"/api/projects/{self.projects['A'].pk}/", {"purpose": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
        p = c.get(f"/api/projects/{self.projects['A'].pk}/").json()
        self.assertEqual(p["status"], "archived")
        self.assertEqual(len(c.get(f"/api/projects/{self.projects['A'].pk}/areas/").json()), 1)   # its areas remain
        self.assertEqual(c.delete(f"/api/projects/{self.projects['A'].pk}/").status_code, 405)   # never deleted
        self.assertTrue(AuditEvent.objects.filter(action="project.archive", object_id=str(self.projects["A"].pk)).exists())


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


class ExportTest(Base):
    def export(self, role, body):
        r = self.client_for("A", role).post(f"/api/projects/{self.projects['A'].pk}/exports/", body, format="json")
        return r

    def unzip(self, r):
        import zipfile
        return zipfile.ZipFile(io.BytesIO(r.content))

    def test_package_contents_and_checksums(self):
        import hashlib
        from unittest import mock
        card = {"model_version": "test", "threshold": 0.41, "score_type": "model score", "tested_in_nigeria": False,
                "domain_warning": "Not yet tested on Nigerian sites (gate G3 pending)."}
        with mock.patch("projects.pipeline.model_card", return_value=card):
            for fmt in ("geojson", "csv", "gpkg"):
                r = self.export("gis", {"format": fmt})
                self.assertEqual(r.status_code, 200, r.content[:200])
                z = self.unzip(r)
                names = set(z.namelist())
                self.assertEqual(names, {f"candidates.{fmt}", "report.pdf", "LICENCE_AND_ATTRIBUTION.txt", "manifest.json",
                                         "verify_export.py", "HOW_TO_VERIFY.txt"})
                man = json.loads(z.read("manifest.json"))
                for n, info in man["files"].items():
                    self.assertEqual(hashlib.sha256(z.read(n)).hexdigest(), info["sha256"])   # FR-109: alteration detectable
                self.assertIn("Contains modified Copernicus Sentinel data 2026", z.read("LICENCE_AND_ATTRIBUTION.txt").decode())
        # CC BY model components are credited from the registry lineage of the model that made the candidates.
        from registry.models import ModelVersion
        ModelVersion.objects.create(name="g2_hn", run_path="/x", input_schema="s", code_commit="c", dataset={},
                                    licences=[{"item": "w", "licence": "CC BY 4.0", "attribution": "Encoder pretrained with SSL4EO-S12"}],
                                    normalisation={}, threshold=0.4, metrics={}, card={},
                                    registered_by=self.users[("A", "gis")])
        with mock.patch("projects.pipeline.model_card", return_value=card):
            txt = self.unzip(self.export("gis", {"format": "geojson"})).read("LICENCE_AND_ATTRIBUTION.txt").decode()
        self.assertIn("Model components:\n  Encoder pretrained with SSL4EO-S12", txt)
        # GeoJSON fields follow FRD 9.2.
        with mock.patch("projects.pipeline.model_card", return_value=card):
            fc = json.loads(self.unzip(self.export("gis", {"format": "geojson"})).read("candidates.geojson"))
            csv_text = self.unzip(self.export("gis", {"format": "csv"})).read("candidates.csv").decode()
            pdf = self.unzip(self.export("gis", {"format": "geojson"})).read("report.pdf")
        props = fc["features"][0]["properties"]
        for k in ("project_id", "detection_id", "crs", "area_ha", "area_method", "scene_ids", "usable_coverage_pct",
                  "model_version", "threshold", "min_mapping_unit_ha", "score_band", "score_type", "review_status",
                  "evidence_grade", "statement"):
            self.assertIn(k, props)
        self.assertEqual(props["score_type"], "model score")
        self.assertIn("geometry_wkt_epsg4326", csv_text.splitlines()[0])   # CSV states its geometry form
        from pypdf import PdfReader
        text = " ".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf)).pages)
        for section in ("Coverage map", "Limits", "Versions", "Evidence grades", "Not yet tested on Nigerian sites",
                        "Contains modified Copernicus Sentinel data"):
            self.assertIn(section, text)
        self.assertTrue(AuditEvent.objects.filter(action="export.create").exists())

    def test_viewer_exports_released_only_and_ml_refused(self):
        from unittest import mock
        card = {"model_version": "t", "threshold": 0.4, "score_type": "model score", "tested_in_nigeria": False, "domain_warning": "x"}
        with mock.patch("projects.pipeline.model_card", return_value=card):
            r = self.export("viewer", {"format": "geojson", "statuses": ["unreviewed"]})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(json.loads(self.unzip(r).read("candidates.geojson"))["features"], [])
        self.assertEqual(self.export("ml", {"format": "geojson"}).status_code, 403)
        self.assertEqual(self.export("gis", {"format": "shp"}).status_code, 400)


class ExportJobSelectionTest(Base):
    def test_latest_job_per_area_by_default_and_explicit_job(self):
        import zipfile
        from unittest import mock
        p, d = self.projects["A"], self.dets["A"]
        newer = Job.objects.create(organisation=p.organisation, project=p, model_version="g2_hn", state="succeeded",
                                   created_by=self.users[("A", "gis")])
        d2 = Detection.objects.get(pk=d.pk); d2.pk = None; d2.job = newer; d2.save()   # same candidate from a rerun
        card = {"model_version": "t", "threshold": 0.4, "score_type": "model score", "tested_in_nigeria": False, "domain_warning": "x"}
        c = self.client_for("A", "gis")
        with mock.patch("projects.pipeline.model_card", return_value=card):
            man = lambda body: json.loads(zipfile.ZipFile(io.BytesIO(c.post(f"/api/projects/{p.pk}/exports/", body,
                                                                                format="json").content)).read("manifest.json"))
            self.assertEqual(man({"format": "csv"})["candidates"], 1)            # not duplicated
            self.assertEqual(man({"format": "csv"})["jobs"], [newer.pk])
            self.assertEqual(man({"format": "csv", "job": d.job_id})["jobs"], [d.job_id])
            other = self.dets["B"].job_id
            self.assertEqual(c.post(f"/api/projects/{p.pk}/exports/", {"format": "csv", "job": other}, format="json").status_code, 404)


class NotAssessedLayerTest(TestCase):
    def test_hatch_only_where_masked(self):
        import numpy as np
        from projects.imagery import not_assessed_rgba
        scl = np.full((12, 12), 4, "uint8"); scl[:, :6] = 9      # left half cloud
        rgba = not_assessed_rgba(scl)
        self.assertTrue((rgba[:, 6:, 3] == 0).all())               # usable: transparent
        self.assertTrue((rgba[:, :6, 3] > 0).all())                # masked: never blank
        self.assertGreater(len(np.unique(rgba[:, :6, 0])), 1)      # striped, not a flat fill


class EditGeometryTest(Base):
    def test_simplified_outline_is_valid_and_small(self):
        import math
        from django.contrib.gis.geos import Polygon as GPolygon
        d = self.dets["A"]
        ring = [(8.8 + 0.01 * math.cos(2 * math.pi * i / 3000) + (0.00005 if i % 2 else 0),
                 9.7 + 0.01 * math.sin(2 * math.pi * i / 3000)) for i in range(3000)]
        g = GPolygon(ring + [ring[0]], srid=4326)
        Detection.objects.filter(pk=d.pk).update(geometry=g)
        r = self.client_for("A", "gis").get(f"/api/detections/{d.pk}/edit-geometry/").json()
        self.assertTrue(r["valid"]); self.assertLessEqual(r["vertices"], 200); self.assertEqual(r["original_vertices"], 3000)
        self.assertEqual(self.client_for("A", "viewer").get(f"/api/detections/{d.pk}/edit-geometry/").status_code, 403)


class RestrictedSitesTest(ExportTest):
    """FR-005: restricted sites are hidden from viewers and exports without an explicit grant."""

    def test_restricted_hidden_without_grant(self):
        import json
        import zipfile
        from unittest import mock
        p, d = self.projects["A"], self.dets["A"]
        Detection.objects.filter(pk=d.pk).update(status="released")
        url = f"/api/detections/{d.pk}/restrict/"
        self.assertEqual(self.client_for("A", "field").post(url, {"restricted": True, "reason": "x"}, format="json").status_code, 403)
        self.assertEqual(self.client_for("A", "gis").post(url, {"restricted": True}, format="json").status_code, 400)   # reason
        self.assertEqual(self.client_for("A", "gis").post(url, {"restricted": True, "reason": "community request"}, format="json").status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(action="detection.restrict").exists())
        viewer = self.client_for("A", "viewer")
        ids = lambda c: [f["id"] for f in c.get(f"/api/projects/{p.pk}/detections/").json()["features"]]
        self.assertEqual(ids(viewer), [])
        self.assertEqual(viewer.get(f"/api/detections/{d.pk}/").status_code, 404)
        self.assertEqual(ids(self.client_for("A", "gis")), [d.pk])   # reviewers still see it, marked restricted
        card = {"model_version": "test", "threshold": 0.41, "score_type": "model score", "tested_in_nigeria": False, "domain_warning": None}
        body = {"format": "geojson", "statuses": ["released"]}
        man = lambda role: json.loads(zipfile.ZipFile(io.BytesIO(self.export(role, body).content)).read("manifest.json"))
        with mock.patch("projects.pipeline.model_card", return_value=card):
            m = man("gis")
            self.assertEqual((m["candidates"], m["restricted_withheld"]), (0, 1))
            grants = f"/api/projects/{p.pk}/restricted-grants/"
            self.assertEqual(self.client_for("A", "gis").post(grants, {"user": "A_gis", "reason": "r"}, format="json").status_code, 403)
            self.assertEqual(self.client_for("A", "admin").post(grants, {"user": "A_gis", "reason": "field partner"}, format="json").status_code, 201)
            self.assertEqual(self.client_for("A", "admin").post(grants, {"user": "B_gis", "reason": "x"}, format="json").status_code, 400)
            m = man("gis")
            self.assertEqual((m["candidates"], m["restricted_withheld"]), (1, 0))
            self.client_for("A", "admin").post(grants, {"user": "A_viewer", "reason": "client agreement"}, format="json")
            self.assertEqual(ids(viewer), [d.pk])
            self.client_for("A", "admin").post(grants, {"user": "A_viewer", "reason": "ended", "revoke": True}, format="json")
            self.assertEqual(ids(viewer), [])


class SignedExportTest(ExportTest):
    """FR-109: a signed package verifies; altering, adding or removing any file, or the manifest, fails."""

    def rezip(self, data, change):
        import zipfile
        src = zipfile.ZipFile(io.BytesIO(data))
        files = {n: src.read(n) for n in src.namelist()}
        change(files)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            for n, b in files.items():
                z.writestr(n, b)
        return out.getvalue()

    def test_signature_and_tampering(self):
        import subprocess, sys, tempfile, zipfile
        from pathlib import Path
        from unittest import mock
        from projects.signing import public_info, public_key, verify
        card = {"model_version": "test", "threshold": 0.41, "score_type": "model score", "tested_in_nigeria": False, "domain_warning": None}
        with mock.patch("projects.pipeline.model_card", return_value=card):
            data = self.export("gis", {"format": "geojson"}).content
        pk = public_key()
        self.assertEqual(verify(data, pk), {**verify(data, pk), "ok": True, "problems": []})
        bad = {
            "altered file": lambda f: f.update({"candidates.geojson": f["candidates.geojson"].replace(b"model score", b"probability!")}),
            "missing file": lambda f: f.pop("report.pdf"),
            "file not in the manifest": lambda f: f.update({"extra.csv": b"x"}),
            "signature does not match": lambda f: f.update({"manifest.json": f["manifest.json"].replace(b'"candidates": 1', b'"candidates": 2')}),
        }
        for problem, change in bad.items():
            r = verify(self.rezip(data, change), pk)
            self.assertFalse(r["ok"], problem)
            self.assertTrue(any(problem in p for p in r["problems"]), (problem, r["problems"]))
        # Re-hashing an altered file in the manifest does not help: the signature then fails.
        def forge(f):
            m = json.loads(f["manifest.json"]); import hashlib
            f["candidates.geojson"] = b"{}"
            m["files"]["candidates.geojson"] = {"sha256": hashlib.sha256(b"{}").hexdigest(), "bytes": 2}
            f["manifest.json"] = json.dumps(m).encode()
        r = verify(self.rezip(data, forge), pk)
        self.assertFalse(r["ok"]); self.assertTrue(any("signature does not match" in p for p in r["problems"]))
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        self.assertFalse(verify(data, Ed25519PrivateKey.generate().public_key())["ok"])   # someone else's key
        # The standalone script inside the package gives the same answer.
        tmp = Path(tempfile.mkdtemp()); (tmp / "p.zip").write_bytes(data)
        (tmp / "verify_export.py").write_bytes(zipfile.ZipFile(io.BytesIO(data)).read("verify_export.py"))
        run = lambda zipname: subprocess.run([sys.executable, str(tmp / "verify_export.py"), str(tmp / zipname), "--public-key",
                                               public_info()["public_key"]], capture_output=True, text=True)
        self.assertEqual(run("p.zip").returncode, 0)
        (tmp / "bad.zip").write_bytes(self.rezip(data, bad["altered file"]))
        r = run("bad.zip"); self.assertEqual(r.returncode, 1); self.assertIn("altered file", r.stdout)
        # And through the API.
        from django.core.files.uploadedfile import SimpleUploadedFile
        c = self.client_for("A", "viewer")
        self.assertEqual(c.get("/api/signing-key/").json()["key_id"], public_info()["key_id"])
        self.assertTrue(c.post("/api/exports/verify/", {"package": SimpleUploadedFile("p.zip", data)}, format="multipart").json()["ok"])


class MonitoringTest(Base):
    """FR-105: a simulated shift in inputs, outputs or review rejections raises an alert, and normal data does not."""

    def test_simulated_shift_raises_alerts(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        from django.contrib.gis.geos import MultiPolygon, Polygon
        from django.core.management import call_command
        from projects.models import AreaOfInterest, Tile
        run = Path(tempfile.mkdtemp())
        bands = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]
        mean, sd = [0.1] * 10, [0.05] * 10
        (run / "normalisation.json").write_text(json.dumps({"bands": bands, "mean": mean, "std": sd}))
        # validation: 100,000 predicted pixels (10 km2) over 1,000 km2 usable -> 10 km2 per 1,000 km2
        (run / "metrics.json").write_text(json.dumps({"models": {"unet": {"val": {"per_site": [{"tp": 60000, "fp": 40000, "valid_km2": 1000}]}}}}))
        p, gis = self.projects["A"], self.users[("A", "gis")]
        scene = Scene.objects.first()

        def job_with(name, means, cand_ha, statuses):
            box = Polygon.from_bbox((8.0, 9.0, 8.9, 9.9)); box.srid = 4326   # about 100 km x 100 km
            a = AreaOfInterest.objects.create(organisation=p.organisation, project=p, name=name, geometry=MultiPolygon(box),
                                              area_km2=9800, created_by=gis)
            j = Job.objects.create(organisation=p.organisation, project=p, area=a, model_version="t", created_by=gis,
                                   state="succeeded", parameters={"model_run": str(run)})
            Tile.objects.create(job=j, grid_id="t", geometry=box, state="done", scene=scene, usable_pct=100.0,
                                outcome="candidate_found", input_stats={"usable_pixels": 1, "means": dict(zip(bands, means))})
            for i, st in enumerate(statuses):
                g = Polygon.from_bbox((8.0 + i / 100, 9.0, 8.005 + i / 100, 9.005)); g.srid = 4326
                Detection.objects.create(organisation=p.organisation, project=p, job=j, scene=scene, geometry=g, original_geometry=g,
                                         area_ha=cand_ha, score_mean=0.6, score_band="medium", threshold=0.4, min_mapping_unit_ha=1,
                                         model_version="t", input_schema_version="s", masked_fraction=0, mask_status="assessed", status=st)
        # normal: inputs at the training mean, about 10 km2 of candidates per 1,000 km2, few rejections
        job_with("Normal", mean, 980, ["confirmed"] * 10)
        # shifted: hazy (all bands +2 SD), far more candidate area, most reviewed candidates rejected
        job_with("Shifted", [m + 2 * s for m, s in zip(mean, sd)], 9800, ["rejected"] * 8 + ["confirmed"] * 2)
        ml = self.client_for("A", "ml")
        self.assertEqual(self.client_for("A", "gis").get("/api/monitoring/").status_code, 403)
        r = ml.get("/api/monitoring/").json()
        kinds = {(a["area"], a["kind"]) for a in r["alerts"]}
        self.assertEqual(kinds, {("Shifted", "input_drift"), ("Shifted", "output_drift"), ("Shifted", "review_rejections")})
        normal = next(g for g in r["groups"] if g["area"] == "Normal")
        self.assertAlmostEqual(normal["output_ratio"], 1.0, delta=0.1)
        self.assertEqual(normal["reference_km2_per_1000km2"], 10.0)
        call_command("check_drift", stdout=io.StringIO())
        self.assertEqual(AuditEvent.objects.filter(action="monitoring.alert").count(), 3)
