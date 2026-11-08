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


def me_payload(u):
    return {"username": u.username, "organisation": u.organisation.name if u.organisation else None,
            "roles": u.roles, "privileged": u.is_privileged, "mfa_enabled": False}


class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        u = authenticate(request, username=request.data.get("username", ""), password=request.data.get("password", ""))
        if u is None or not u.is_active:
            return Response({"detail": "wrong username or password"}, status=400)
        login(request, u)
        return Response(me_payload(u))


class LogoutView(APIView):
    def post(self, request):
        record(request.user, "auth.logout")
        logout(request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(me_payload(request.user))


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    record(user, "auth.login")


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    record(None, "auth.login_failed", after={"username": credentials.get("username", "")[:150]})
