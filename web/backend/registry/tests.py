"""FR-101 to FR-104: lineage required, comparison before promotion, two-person approval, rollback."""
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Organisation, User
from audit.models import AuditEvent
from registry.models import ModelVersion


def fake_run(root: Path, name: str, f1_shift=0.0, commit="abc123", schema="s2-l2a-10band-v1"):
    run = root / name
    run.mkdir(parents=True)
    rows = lambda: [{"site_id": f"s{i}", "tp": 50 + i + int(100 * f1_shift), "fp": 10, "fn": 40 - int(100 * f1_shift) - i % 3,
                     "tn": 1000, "label_footprints": 2, "found_any_overlap": 1, "covered_25pct": 1, "false_candidates": 1,
                     "valid_km2": 10.0, "label_pixels": 90 + i - i % 3, "label_pixels_masked": 0} for i in range(20)]
    strata = {"all": {"f1": 0.5 + f1_shift, "precision": 0.5, "recall": 0.5, "false_alarm_km2_per_1000km2": 10.0}}
    (run / "metrics.json").write_text(json.dumps({"models": {"unet": {
        "threshold_score": 0.4, "val": {"strata": strata, "per_site": rows()}, "test": {"strata": strata, "per_site": rows()}}}}))
    (run / "config.json").write_text(json.dumps({
        "code_commit": commit, "dataset": {"name": "global_ml_ready", "version": "1.0.0", "licence": "CC BY-SA 4.0"},
        "input_schema": schema, "split_file_sha256": "f" * 64, "sites": {"train": ["a"]}, "params": {"epochs": 1},
        "encoder_weights_note": "x"}))
    (run / "normalisation.json").write_text(json.dumps({"mean": [0] * 10, "std": [1] * 10}))
    (run / "unet_best.pt").write_bytes(b"weights")
    return run


class RegistryTest(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ov = override_settings(MINEFOOT={**settings.MINEFOOT, "DATA_ROOT": self.tmp})
        self.ov.enable()
        org = Organisation.objects.create(name="O")
        self.ml1 = User.objects.create_user("ml1", password="pw-long-enough", organisation=org, roles=["ml"])
        self.ml2 = User.objects.create_user("ml2", password="pw-long-enough", organisation=org, roles=["ml"])
        self.adm = User.objects.create_user("adm", password="pw-long-enough", organisation=org, roles=["admin"])
        self.gis = User.objects.create_user("gis", password="pw-long-enough", organisation=org, roles=["gis"])

    def tearDown(self):
        self.ov.disable()

    def c(self, u):
        c = APIClient(); c.force_login(u)
        if u.is_privileged:
            User.objects.filter(pk=u.pk).update(mfa_enabled=True)
            s = c.session; s["mfa_verified"] = True; s.save()
        return c

    def reg(self, u, run, name=None):
        return self.c(u).post("/api/models/", {"run": run, **({"name": name} if name else {})}, format="json")

    def act(self, u, pk, action, body=None):
        return self.c(u).post(f"/api/models/{pk}/{action}/", body or {}, format="json")

    def approve(self, u, pk, scope="internal_research"):
        ids = [i["id"] for i in self.c(u).get(f"/api/models/{pk}/release-review/").json()["issues"]]
        return self.act(u, pk, "approve", {"scope": scope, "reasons": {i: "accepted for internal tests, reviewed by hand" for i in ids}})

    def test_lineage_required(self):
        fake_run(self.tmp / "runs", "dirty", commit="abc+uncommitted")
        self.assertIn("uncommitted", self.reg(self.ml1, "dirty").json()["detail"])
        r = fake_run(self.tmp / "runs", "nocommit"); cfg = json.loads((r / "config.json").read_text())
        cfg["code_commit"] = ""; (r / "config.json").write_text(json.dumps(cfg))
        self.assertIn("code_commit", self.reg(self.ml1, "nocommit").json()["detail"])
        self.assertEqual(self.reg(self.ml1, "../../etc").status_code, 400)          # no arbitrary paths
        self.assertEqual(self.reg(self.gis, "dirty").status_code, 403)

    def test_compare_propose_two_person_approve_and_rollback(self):
        fake_run(self.tmp / "runs", "m1"); fake_run(self.tmp / "runs", "m2", f1_shift=0.1)
        m1 = self.reg(self.ml1, "m1").json()
        self.assertEqual(m1["state"], "experimental")
        self.assertIn("pretrained encoder weights", json.dumps(m1["licences"]))
        self.assertIn("propose", self.act(self.ml1, m1["id"], "propose").json()["detail"])   # not under evaluation yet
        self.act(self.ml1, m1["id"], "compare")
        self.assertEqual(self.act(self.ml1, m1["id"], "propose").json()["state"], "candidate")
        self.assertIn("second person", self.approve(self.ml1, m1["id"]).json()["detail"])   # self-approval refused
        self.assertEqual(self.approve(self.ml2, m1["id"]).json()["state"], "production")
        m2 = self.reg(self.ml1, "m2").json()
        rep = self.c(self.ml1).post(f"/api/models/{m2['id']}/compare/", {}, format="json").json()["reports"][0]
        self.assertEqual(rep["baseline"], "m1"); self.assertEqual(rep["sites"], 20)
        self.assertEqual(rep["result"]["metrics"]["f1"]["verdict"], "b better")
        self.act(self.ml1, m2["id"], "propose")
        self.assertEqual(self.approve(self.adm, m2["id"]).json()["state"], "production")
        self.assertEqual(ModelVersion.objects.get(name="m1").state, "retired")
        from projects.pipeline import active_model
        self.assertEqual(active_model()[1], "m2")                                     # jobs use production
        r = self.act(self.adm, m2["id"], "rollback")
        self.assertEqual(r.json()["name"], "m1")
        self.assertEqual(ModelVersion.objects.get(name="m1").state, "production")
        self.assertEqual(ModelVersion.objects.get(name="m2").state, "rolled_back")
        for a in ("model.register", "model.compare", "model.propose", "model.approve", "model.rollback"):
            self.assertTrue(AuditEvent.objects.filter(action=a).exists(), a)

    def test_schema_mismatch_refused(self):
        fake_run(self.tmp / "runs", "old", schema="s2-l2a-4band-v0")
        m = self.reg(self.ml1, "old").json()
        self.act(self.ml1, m["id"], "compare")
        self.assertIn("schema", self.act(self.ml1, m["id"], "propose").json()["detail"])
        from projects.pipeline import check_schema
        with self.assertRaises(ValueError):
            check_schema(self.tmp / "runs" / "old")

    def test_dashboard_reads_stored_records(self):
        """FR-130: chart values are the run's own records; missing curves are reported, not invented."""
        run = fake_run(self.tmp / "runs", "r1")
        (run / "history.csv").write_text("epoch,train_loss,val_chip_loss,lr,elapsed_s,checkpoint\n"
                                         "1,0.9,0.8,0.001,10,True\n2,0.7,0.85,0.0009,20,False\n")
        pk = self.reg(self.ml1, "r1").json()["id"]
        self.assertEqual(self.c(self.gis).get(f"/api/models/{pk}/dashboard/").status_code, 403)
        d = self.c(self.ml1).get(f"/api/models/{pk}/dashboard/").json()
        self.assertEqual([h["val_chip_loss"] for h in d["history"]], [0.8, 0.85])
        self.assertEqual(d["metrics"]["strata"]["test"]["all"]["f1"], 0.5)
        self.assertIsNone(d["curves"])
        self.assertTrue(any("curves.json" in m for m in d["missing"]))
        (run / "curves.json").write_text(json.dumps({"created_at": "t", "models": {"unet": {
            k: {"pr": [{"level": 1, "score": 0.0, "precision": 0.1, "recall": 1.0}], "reliability": [],
                "at_threshold": {"precision": 0.5, "recall": 0.5}} for k in ("val", "test")}}}))
        d = self.c(self.ml1).get(f"/api/models/{pk}/dashboard/").json()
        self.assertEqual(d["curves"]["test"]["pr"][0]["precision"], 0.1)
        self.assertEqual(d["missing"], [])

    def test_error_maps_ranked_and_rendered(self):
        """FR-129: test sites ranked by worst F1; the image shows the stored error classes, each one switchable."""
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        run = fake_run(self.tmp / "runs", "r1")
        (run / "error_maps").mkdir()
        e = np.zeros((40, 40), "uint8"); e[:10] = 1; e[10:20] = 2; e[20:30] = 3; e[30:] = 4
        for sid in ("s0", "s5"):
            with rasterio.open(run / "error_maps" / f"{sid}__unet.tif", "w", driver="GTiff", width=40, height=40, count=1,
                               dtype="uint8", crs="EPSG:32631", transform=from_origin(500000, 1000000, 10, 10)) as d:
                d.write(e, 1)
        pk = self.reg(self.ml1, "r1").json()["id"]
        with override_settings(MINEFOOT={**settings.MINEFOOT, "DATA_ROOT": self.tmp, "IMAGE_CACHE": self.tmp / "cache"}):
            c = self.c(self.ml1)
            self.assertEqual(self.c(self.gis).get(f"/api/models/{pk}/errors/").status_code, 403)
            sites = c.get(f"/api/models/{pk}/errors/").json()["sites"]
            self.assertEqual([s["site_id"] for s in sites], ["s0", "s5"])   # only sites with maps; s0 has the lower F1
            self.assertLessEqual(sites[0]["f1"], sites[1]["f1"])
            self.assertEqual(c.get(f"/api/models/{pk}/errors/../../x/image/").status_code, 404)
            self.assertEqual(c.get(f"/api/models/{pk}/errors/nope/image/").status_code, 400)
            from io import BytesIO
            from PIL import Image
            def colours(q):
                r = c.get(f"/api/models/{pk}/errors/s0/image/?{q}")
                px = np.asarray(Image.open(BytesIO(b"".join(r.streaming_content))))
                return {tuple(v[:3]) for v in px.reshape(-1, 4) if v[3] > 200}
            allc = colours("classes=hit,false_alarm,missed")
            self.assertEqual(allc, {(0, 114, 178), (230, 159, 0), (204, 121, 167)})
            self.assertEqual(colours("classes=missed"), {(204, 121, 167)})
            self.assertEqual(len(c.get(f"/api/models/{pk}/errors/s0/image/?bounds=1").json()["bounds"]), 4)


    def test_release_review_scope_and_reasons(self):
        """FR-104: approval names a scope; open issues need written reasons; unresolved licences and an untested
        Nigerian domain block commercial approval; jobs refuse projects beyond the approved scope."""
        fake_run(self.tmp / "runs", "m1")
        pk = self.reg(self.ml1, "m1").json()["id"]
        self.act(self.ml1, pk, "compare"); self.act(self.ml1, pk, "propose")
        rv = self.c(self.ml2).get(f"/api/models/{pk}/release-review/").json()
        ids = {i["id"] for i in rv["issues"]}
        self.assertTrue({"licence:pretrained encoder weights", "licence:global_ml_ready", "domain:nigeria"} <= ids, ids)
        self.assertNotIn("commercial", rv["allowed_scopes"])
        r = self.act(self.ml2, pk, "approve", {"scope": "commercial", "reasons": {i: "x" * 30 for i in ids}})
        self.assertIn("cannot approve for commercial", r.json()["detail"])
        r = self.act(self.ml2, pk, "approve", {"scope": "evaluation", "reasons": {"domain:nigeria": "too short"}})
        self.assertIn("write a reason", r.json()["detail"])
        self.assertEqual(ModelVersion.objects.get(pk=pk).state, "candidate")
        r = self.act(self.ml2, pk, "approve", {"scope": "evaluation", "reasons": {i: "accepted: evaluation with partners only" for i in ids}})
        self.assertEqual((r.json()["state"], r.json()["approved_scope"]), ("production", "evaluation"))
        ev = AuditEvent.objects.filter(action="model.approve").last()
        self.assertEqual(ev.after["scope"], "evaluation"); self.assertIn("domain:nigeria", ev.after["reasons"])
        from registry.release import covers
        self.assertTrue(covers("evaluation", "internal_research")); self.assertFalse(covers("evaluation", "commercial"))
