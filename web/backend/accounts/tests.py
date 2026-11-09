"""FR-001: multi-factor sign-in for privileged roles."""
import pyotp
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Organisation, User, get_mfa_secret
from audit.models import AuditEvent


class MfaTest(TestCase):
    def setUp(self):
        org = Organisation.objects.create(name="O")
        self.admin = User.objects.create_user("adm", password="pw-long-enough", organisation=org, roles=["admin"])
        self.gis = User.objects.create_user("gis", password="pw-long-enough", organisation=org, roles=["gis"])

    def login(self, c, user):
        return c.post("/api/auth/login/", {"username": user, "password": "pw-long-enough"}, format="json")

    def test_admin_without_second_factor_is_refused(self):
        c = APIClient()
        r = self.login(c, "adm")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["mfa_satisfied"])
        r = c.get("/api/audit/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Multi-factor", r.json()["detail"])
        self.assertEqual(c.get("/api/me/").status_code, 200)          # can still see who they are and enrol

    def test_enrol_then_sign_in_needs_code(self):
        c = APIClient()
        self.login(c, "adm")
        setup = c.post("/api/auth/mfa/setup/").json()
        self.assertTrue(setup["qr_png"].startswith("data:image/png;base64,"))
        self.admin.refresh_from_db()
        self.assertNotIn(setup["secret"], self.admin.mfa_secret_encrypted)   # encrypted at rest
        self.assertEqual(get_mfa_secret(self.admin), setup["secret"])
        self.assertEqual(c.post("/api/auth/mfa/confirm/", {"code": "000000"}, format="json").status_code, 400)
        code = pyotp.TOTP(setup["secret"]).now()
        r = c.post("/api/auth/mfa/confirm/", {"code": code}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["mfa_satisfied"])
        self.assertEqual(c.get("/api/audit/").status_code, 200)
        # A new sign-in: the password alone does not sign in.
        c2 = APIClient()
        r = self.login(c2, "adm")
        self.assertEqual(r.json(), {"mfa_required": True})
        self.assertIn(c2.get("/api/me/").status_code, (401, 403))
        self.assertEqual(c2.post("/api/auth/mfa/verify/", {"code": "123456"}, format="json").status_code, 400)
        r = c2.post("/api/auth/mfa/verify/", {"code": pyotp.TOTP(setup["secret"]).now()}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c2.get("/api/audit/").status_code, 200)
        for action in ("auth.mfa_enrolled", "auth.mfa_failed", "auth.mfa_verified"):
            self.assertTrue(AuditEvent.objects.filter(action=action).exists(), action)

    def test_wrong_codes_lock_the_attempt(self):
        c = APIClient()
        self.login(c, "adm")
        secret = c.post("/api/auth/mfa/setup/").json()["secret"]
        c.post("/api/auth/mfa/confirm/", {"code": pyotp.TOTP(secret).now()}, format="json")
        c2 = APIClient()
        self.login(c2, "adm")
        for _ in range(5):
            c2.post("/api/auth/mfa/verify/", {"code": "000000"}, format="json")
        r = c2.post("/api/auth/mfa/verify/", {"code": pyotp.TOTP(secret).now()}, format="json")
        self.assertEqual(r.status_code, 400)                          # locked: sign in again
        self.assertTrue(AuditEvent.objects.filter(action="auth.mfa_locked").exists())

    def test_non_privileged_roles_do_not_need_it(self):
        c = APIClient()
        r = self.login(c, "gis")
        self.assertTrue(r.json()["mfa_satisfied"])
        self.assertEqual(c.get("/api/projects/").status_code, 200)
