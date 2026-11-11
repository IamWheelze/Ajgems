"""FR-093 missed sites and FR-094 review filters."""
from datetime import datetime, timezone

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from projects.models import AreaOfInterest, Detection, Job, Scene, Tile
from projects.tests import Base
from review.models import MissedSite, Review
from review.services import submit_review


def box(x0, y0, x1, y1):
    p = Polygon.from_bbox((x0, y0, x1, y1)); p.srid = 4326
    return p


def gj(p):
    import json
    return json.loads(p.geojson)


class MissedSiteTest(Base):
    def setUp(self):
        super().setUp()
        p = self.projects["A"]; gis = self.users[("A", "gis")]
        self.area = AreaOfInterest.objects.create(organisation=p.organisation, project=p, name="aoi", geometry=MultiPolygon(box(8.80, 9.70, 8.90, 9.80)),
                                                  area_km2=120.0, created_by=gis)
        self.job = Job.objects.create(organisation=p.organisation, project=p, area=self.area, model_version="g2_hn",
                                      created_by=gis, state="succeeded", finished_at=datetime.now(timezone.utc))
        scene = Scene.objects.first()
        Tile.objects.create(job=self.job, grid_id="t1", geometry=box(8.80, 9.70, 8.85, 9.80), state="done", scene=scene,
                            outcome="no_candidate_found_in_usable_imagery")
        Tile.objects.create(job=self.job, grid_id="t2", geometry=box(8.85, 9.70, 8.90, 9.80), state="done",
                            outcome="insufficient_usable_imagery")
        self.url = f"/api/projects/{p.pk}/missed-sites/"

    def test_add_list_and_count_separately(self):
        c = self.client_for("A", "gis")
        r = c.post(self.url, {"geometry": gj(box(8.81, 9.71, 8.82, 9.72)), "reason_code": "not_detected"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        f = r.json()["properties"]
        self.assertEqual((f["job"], f["tile_outcome"], f["counts_as"]), (self.job.pk, "no_candidate_found_in_usable_imagery", "false negative"))
        self.assertAlmostEqual(f["area_ha"], 121.4, delta=0.5)   # 0.01 degree square near 9.7 N: 1.097 km x 1.106 km
        r = c.post(self.url, {"geometry": gj(box(8.86, 9.71, 8.87, 9.72)), "reason_code": "not_detected"}, format="json")
        self.assertIn("not a model miss", r.json()["properties"]["counts_as"])   # FR-052: no usable imagery there
        self.assertEqual(len(c.get(self.url).json()["features"]), 2)
        self.assertEqual(Detection.objects.filter(project=self.projects["A"]).count(), 1)   # not mixed with candidates
        import io, json, zipfile
        from projects.exports import build_package
        data, manifest = build_package(self.projects["A"], [], self.users[("A", "gis")], "geojson", ["confirmed"])
        self.assertEqual(manifest["missed_sites"], 2)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(len(json.loads(z.read("missed_sites.geojson"))["features"]), 2)

    def test_rules(self):
        c = self.client_for("A", "gis")
        bad = [({"geometry": gj(box(8.81, 9.71, 8.82, 9.72)), "reason_code": "nope"}, 400),
               ({"geometry": gj(box(8.81, 9.71, 8.82, 9.72)), "reason_code": "other"}, 400),          # other needs a note
               ({"geometry": gj(box(7.0, 7.0, 7.1, 7.1)), "reason_code": "not_detected"}, 400),       # outside any job area
               ({"geometry": {"type": "Polygon", "coordinates": [[[8.81, 9.71], [8.82, 9.72], [8.82, 9.71], [8.81, 9.72], [8.81, 9.71]]]},
                 "reason_code": "not_detected"}, 400)]                                                  # bow-tie
        for body, code in bad:
            self.assertEqual(c.post(self.url, body, format="json").status_code, code, body)
        ok = {"geometry": gj(box(8.81, 9.71, 8.82, 9.72)), "reason_code": "not_detected"}
        self.assertEqual(self.client_for("A", "field").post(self.url, ok, format="json").status_code, 403)
        self.assertEqual(self.client_for("A", "viewer").get(self.url).status_code, 403)
        self.assertEqual(self.client_for("B", "gis").post(self.url, ok, format="json").status_code, 404)   # FR-003

    def test_append_only(self):
        self.client_for("A", "gis").post(self.url, {"geometry": gj(box(8.81, 9.71, 8.82, 9.72)), "reason_code": "not_detected"}, format="json")
        ms = MissedSite.objects.get()
        with self.assertRaises(PermissionError):
            ms.delete()
        with self.assertRaises(ProgrammingError), transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE review_missedsite SET area_ha = 0")


class FilterTest(Base):
    """FR-094: filter results match a direct database query."""

    def test_filters_match_database(self):
        p, d0 = self.projects["A"], self.dets["A"]
        scene2 = Scene.objects.create(source_id="S2_LATER", provider="test", sensor="sentinel-2a",
                                      acquired_at=datetime(2026, 3, 1, tzinfo=timezone.utc), processing_baseline="05.11",
                                      cloud_pct=0.1, licence_note="x")
        for i, (ha, band, model, sc) in enumerate([(3.0, "low", "g2_v1", scene2), (40.0, "medium", "g2_hn", scene2),
                                                   (8.0, "high", "g2_v1", d0.scene)]):
            g = box(8.82 + i / 100, 9.70, 8.825 + i / 100, 9.705)
            Detection.objects.create(organisation=p.organisation, project=p, job=d0.job, scene=sc, geometry=g, original_geometry=g,
                                     area_ha=ha, score_mean=0.5, score_band=band, threshold=0.41, min_mapping_unit_ha=1.0,
                                     model_version=model, input_schema_version="s", masked_fraction=0.0, mask_status="assessed")
        gis = self.users[("A", "gis")]
        submit_review(gis, d0.pk, "reject", "farmland")
        c = self.client_for("A", "gis")
        base = Detection.objects.filter(project=p)
        cases = [("score_band=low,medium", base.filter(score_band__in=["low", "medium"])),
                 ("min_ha=5&max_ha=50", base.filter(area_ha__gte=5, area_ha__lte=50)),
                 ("date_from=2026-02-01", base.filter(scene__acquired_at__date__gte="2026-02-01")),
                 ("model_version=g2_v1", base.filter(model_version="g2_v1")),
                 ("reason=farmland", base.filter(pk__in=Review.objects.filter(reason_code="farmland").values("detection"))),
                 ("status=unreviewed&score_band=high", base.filter(status="unreviewed", score_band="high"))]
        for q, expected in cases:
            got = {f["id"] for f in c.get(f"/api/projects/{p.pk}/detections/?{q}").json()["features"]}
            self.assertEqual(got, set(expected.values_list("pk", flat=True)), q)
            self.assertTrue(got, q)


class PoolTest(Base):
    """FR-075: reviewed output reaches training labels only through an independent check."""

    def setUp(self):
        super().setUp()
        from django.conf import settings
        from django.test import override_settings
        self.ov = override_settings(MINEFOOT={**settings.MINEFOOT, "NIGERIA_AOIS": "/nonexistent.yaml"})
        self.ov.enable()
        from accounts.models import User
        org = self.orgs["A"]
        self.gis2 = User.objects.create_user("A_gis2", password="pw-long-enough", organisation=org, roles=["gis"])
        p, d0 = self.projects["A"], self.dets["A"]
        self.extra = []
        for i in range(2):
            g = box(8.85 + i / 100, 9.70, 8.855 + i / 100, 9.705)
            self.extra.append(Detection.objects.create(
                organisation=org, project=p, job=d0.job, scene=d0.scene, geometry=g, original_geometry=g, area_ha=5.0,
                score_mean=0.6, score_band="medium", threshold=0.41, min_mapping_unit_ha=1.0, model_version="g2_hn",
                input_schema_version="s", masked_fraction=0.0, mask_status="assessed"))

    def tearDown(self):
        self.ov.disable()

    def c2(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_login(self.gis2)
        return c

    def test_independent_qc_only_path(self):
        gis, d = self.client_for("A", "gis"), self.dets["A"]
        self.assertEqual(gis.post("/api/pool/", {"detection": d.pk}, format="json").status_code, 400)   # not reviewed yet
        submit_review(self.users[("A", "gis")], d.pk, "confirm", "visible_disturbance")
        r = gis.post("/api/pool/", {"detection": d.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        nid = r.json()["properties"]["id"]
        self.assertEqual(r.json()["properties"]["kind"], "mining")
        self.assertEqual(gis.post("/api/pool/", {"detection": d.pk}, format="json").status_code, 400)   # no duplicates
        accept = {"decision": "accept", "reason_code": "matches_imagery"}
        self.assertEqual(gis.post(f"/api/pool/{nid}/decide/", accept, format="json").status_code, 403)   # own nomination
        self.assertEqual(self.client_for("A", "field").post(f"/api/pool/{nid}/decide/", accept, format="json").status_code, 403)
        self.assertEqual(self.client_for("B", "ml").post(f"/api/pool/{nid}/decide/", accept, format="json").status_code, 404)
        ml = self.client_for("A", "ml")
        r = ml.post(f"/api/pool/{nid}/decide/", accept, format="json")
        self.assertEqual((r.status_code, r.json()["properties"]["state"]), (200, "accepted"))
        self.assertEqual(ml.post(f"/api/pool/{nid}/decide/", accept, format="json").status_code, 400)   # decided once

        # A reviewer of the detection is not independent either, even though someone else nominated it.
        neg = self.extra[0]
        submit_review(self.gis2, neg.pk, "reject", "farmland")
        r = gis.post("/api/pool/", {"detection": neg.pk}, format="json")
        self.assertEqual((r.json()["properties"]["kind"], r.json()["properties"]["negative_type"]), ("hard_negative", "farmland"))
        nid2 = r.json()["properties"]["id"]
        self.assertEqual(self.c2().post(f"/api/pool/{nid2}/decide/", accept, format="json").status_code, 403)
        self.assertEqual(ml.post(f"/api/pool/{nid2}/decide/", {"decision": "return", "reason_code": "outline_inaccurate"},
                                 format="json").json()["properties"]["state"], "returned")
        # A rejection reason that names no negative type cannot become a negative.
        submit_review(self.gis2, self.extra[1].pk, "reject", "cloud_or_shadow")
        self.assertEqual(gis.post("/api/pool/", {"detection": self.extra[1].pk}, format="json").status_code, 400)

        # Export: accepted examples only, ML role only.
        self.assertEqual(gis.get("/api/pool/export/").status_code, 403)
        import io, json, zipfile
        z = zipfile.ZipFile(io.BytesIO(ml.get("/api/pool/export/").content))
        labels, manifest = json.loads(z.read("labels.geojson")), json.loads(z.read("manifest.json"))
        self.assertEqual([f["properties"]["example_id"] for f in labels["features"]], [f"pool-{nid}"])
        self.assertEqual((manifest["examples"], manifest["mining"], manifest["hard_negative"]), (1, 1, 0))
        from review.models import Nomination, QCDecision
        with self.assertRaises(PermissionError):
            QCDecision.objects.first().delete()
        self.assertEqual(Nomination.objects.count(), 2)

    def test_nigerian_test_regions_never_enter_the_pool(self):
        self.ov.disable()   # real config: the demo detections sit inside the Jos Plateau test region
        d = self.dets["A"]
        submit_review(self.users[("A", "gis")], d.pk, "confirm", "visible_disturbance")
        r = self.client_for("A", "gis").post("/api/pool/", {"detection": d.pk}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ML-07", r.json()["detail"])
        self.ov.enable()


class FieldEvidenceTest(Base):
    """FR-095: offline visits sync once, carry the field decision, lose photo metadata, and photos follow role rules."""

    def setUp(self):
        super().setUp()
        import tempfile
        from pathlib import Path
        from django.conf import settings
        from django.test import override_settings
        self.ov = override_settings(MINEFOOT={**settings.MINEFOOT, "DATA_ROOT": Path(tempfile.mkdtemp())})
        self.ov.enable()

    def tearDown(self):
        self.ov.disable()

    def photo_with_gps(self):
        import io
        from PIL import Image
        im = Image.new("RGB", (64, 48), (120, 90, 60))
        exif = Image.Exif()
        exif[0x010F] = "PhoneMaker"                                   # Make
        exif[0x8825] = {1: "N", 2: (9.0, 45.0, 0.0), 3: "E", 4: (8.0, 52.0, 0.0)}   # GPSInfo
        b = io.BytesIO(); im.save(b, "JPEG", exif=exif.tobytes())
        assert Image.open(io.BytesIO(b.getvalue())).getexif()          # the input really has metadata
        b.seek(0); b.name = "IMG_0001.jpg"
        return b

    def test_offline_visit_sync(self):
        import io, json, uuid
        from PIL import Image
        from review.models import FieldPhoto, FieldVisit
        d = self.dets["A"]
        submit_review(self.users[("A", "gis")], d.pk, "request_field_check", "needs_ground_truth")
        field = self.client_for("A", "field")
        tasks = field.get("/api/field/tasks/").json()
        self.assertEqual([t["id"] for t in tasks], [d.pk])
        cid = str(uuid.uuid4())
        meta = {"client_uuid": cid, "detection": d.pk, "visited_at": "2026-11-10T09:30:00+01:00", "lat": 9.75, "lon": 8.87,
                "accuracy_m": 6, "notes": "fresh pits, no activity seen", "observer_name": "A. Field",
                "decision": {"action": "field_confirm", "reason_code": "observed_on_site"}}
        body = lambda: {"meta": json.dumps(meta), "blurred": json.dumps([2]), "photos": [self.photo_with_gps()]}
        self.assertEqual(self.client_for("A", "gis").post("/api/field/visits/", body(), format="multipart").status_code, 403)
        r = field.post("/api/field/visits/", body(), format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        d.refresh_from_db()
        self.assertEqual((d.status, d.evidence_grade), ("field_confirmed", "E3"))
        self.assertEqual(field.post("/api/field/visits/", body(), format="multipart").status_code, 200)   # retried sync
        self.assertEqual((FieldVisit.objects.count(), FieldPhoto.objects.count()), (1, 1))
        p = FieldPhoto.objects.get()
        self.assertEqual(p.blurred_regions, 2)
        stored = Image.open(p.path)
        self.assertEqual(dict(stored.getexif()), {})                  # no GPS, maker or serial left in the file
        pid = r.json()["photos"][0]["id"]
        self.assertEqual(self.client_for("A", "viewer").get(f"/api/field/photos/{pid}/").status_code, 403)
        self.assertEqual(self.client_for("B", "field").get(f"/api/field/photos/{pid}/").status_code, 404)
        self.assertEqual(self.client_for("A", "gis").get(f"/api/field/photos/{pid}/").status_code, 200)
        visits = self.client_for("A", "gis").get(f"/api/detections/{d.pk}/field-visits/").json()
        self.assertEqual((visits[0]["decision"]["action"], visits[0]["lat"]), ("field_confirm", 9.75))
        # A decision the rules refuse stores nothing, so the phone keeps the visit queued with the reason.
        meta2 = {**meta, "client_uuid": str(uuid.uuid4())}
        r = field.post("/api/field/visits/", {"meta": json.dumps(meta2), "photos": []}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(FieldVisit.objects.count(), 1)
