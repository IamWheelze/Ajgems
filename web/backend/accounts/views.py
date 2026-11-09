"""Sign-in with session cookies, and who-am-I (FR-001, FR-004).

Multi-factor sign-in for privileged roles (FR-001) is not built yet: privileged users
are flagged in /api/me so the interface can say so.
"""
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver
from django.middleware.csrf import get_token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import record


def me_payload(u, request=None):
    from .permissions import second_factor_ok
    return {"username": u.username, "organisation": u.organisation.name if u.organisation else None,
            "roles": u.roles, "privileged": u.is_privileged, "mfa_enabled": u.mfa_enabled,
            "mfa_satisfied": second_factor_ok(request) if request is not None else not u.is_privileged}


class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    """Password first. Users with multi-factor enabled then send their code to /api/auth/mfa/verify/."""
    permission_classes = [AllowAny]

    def post(self, request):
        u = authenticate(request, username=request.data.get("username", ""), password=request.data.get("password", ""))
        if u is None or not u.is_active:
            return Response({"detail": "wrong username or password"}, status=400)
        if u.mfa_enabled:
            request.session.flush()
            request.session["mfa_pending_user"] = u.pk          # not signed in until the code is verified
            return Response({"mfa_required": True})
        login(request, u)
        request.session["mfa_verified"] = False
        return Response(me_payload(u, request))


def _totp(user):
    import pyotp
    from .models import get_mfa_secret
    return pyotp.TOTP(get_mfa_secret(user))


class MfaVerifyView(APIView):
    """Second step of sign-in: a 6-digit code from the authenticator app (FR-001)."""
    permission_classes = [AllowAny]

    def post(self, request):
        from .models import User
        pk = request.session.get("mfa_pending_user")
        if not pk:
            return Response({"detail": "sign in with your password first"}, status=400)
        u = User.objects.get(pk=pk)
        attempts = request.session.get("mfa_attempts", 0)
        if attempts >= 5:
            request.session.flush()
            record(u, "auth.mfa_locked")
            return Response({"detail": "too many wrong codes; sign in again"}, status=400)
        if not _totp(u).verify(str(request.data.get("code", "")).strip(), valid_window=1):
            request.session["mfa_attempts"] = attempts + 1
            record(u, "auth.mfa_failed")
            return Response({"detail": "wrong code"}, status=400)
        login(request, u, backend="django.contrib.auth.backends.ModelBackend")
        request.session["mfa_verified"] = True
        record(u, "auth.mfa_verified")
        return Response(me_payload(u, request))


class MfaSetupView(APIView):
    """Start enrolment: a new secret and a QR code for an authenticator app. Signed-in users only."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import base64, io
        import pyotp, qrcode
        from .models import set_mfa_secret
        u = request.user
        if u.mfa_enabled:
            return Response({"detail": "multi-factor sign-in is already set up"}, status=400)
        secret = pyotp.random_base32()
        set_mfa_secret(u, secret)
        u.save(update_fields=["mfa_secret_encrypted"])
        uri = pyotp.TOTP(secret).provisioning_uri(name=u.username, issuer_name="minefoot")
        buf = io.BytesIO(); qrcode.make(uri).save(buf, "PNG")
        return Response({"secret": secret, "otpauth_uri": uri,
                         "qr_png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})


class MfaConfirmView(APIView):
    """Finish enrolment with a first valid code; the session then counts as verified."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        u = request.user
        if not u.mfa_secret_encrypted or u.mfa_enabled:
            return Response({"detail": "start set-up first"}, status=400)
        if not _totp(u).verify(str(request.data.get("code", "")).strip(), valid_window=1):
            record(u, "auth.mfa_enrol_failed")
            return Response({"detail": "wrong code; check the time on your phone and try again"}, status=400)
        u.mfa_enabled = True
        u.save(update_fields=["mfa_enabled"])
        request.session["mfa_verified"] = True
        record(u, "auth.mfa_enrolled")
        return Response(me_payload(u, request))


class LogoutView(APIView):
    def post(self, request):
        record(request.user, "auth.logout")
        logout(request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(me_payload(request.user, request))


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    record(user, "auth.login")


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    record(None, "auth.login_failed", after={"username": credentials.get("username", "")[:150]})
