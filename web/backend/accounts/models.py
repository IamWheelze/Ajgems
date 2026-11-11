"""Organisations and users with roles (FRD 5, 9.1, FR-001 to FR-003)."""
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    ML = "ml", "ML/data administrator"
    GIS = "gis", "GIS analyst"
    FIELD = "field", "Field reviewer"
    VIEWER = "viewer", "Customer viewer"


PRIVILEGED_ROLES = {Role.ADMIN, Role.ML}   # FR-001: these will need multi-factor sign-in


class Organisation(models.Model):
    name = models.CharField(max_length=200, unique=True)
    region = models.CharField(max_length=100, blank=True)
    max_aoi_km2 = models.FloatField(null=True, blank=True, help_text="Overrides the default FR-013 limit")
    # FR-087 quotas; empty means no limit.
    quota_km2_per_month = models.FloatField(null=True, blank=True, help_text="area processed by jobs started this month")
    quota_jobs_per_month = models.IntegerField(null=True, blank=True)
    quota_storage_gb = models.FloatField(null=True, blank=True, help_text="job imagery and score maps kept on disk")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    """A person in exactly one organisation, holding one or more roles."""
    organisation = models.ForeignKey(Organisation, on_delete=models.PROTECT, related_name="users", null=True)
    roles = models.JSONField(default=list, blank=True, help_text="List of Role values")
    mfa_secret_encrypted = models.TextField(blank=True, help_text="TOTP secret, Fernet-encrypted (FR-001)")
    mfa_enabled = models.BooleanField(default=False)

    def has_role(self, *roles) -> bool:
        return any(r in (self.roles or []) for r in roles)

    @property
    def is_privileged(self) -> bool:
        return any(r in PRIVILEGED_ROLES for r in (self.roles or []))

    def clean(self):
        from django.core.exceptions import ValidationError
        bad = [r for r in self.roles or [] if r not in Role.values]
        if bad:
            raise ValidationError({"roles": f"unknown roles {bad}"})


def _fernet():
    import base64
    import hashlib
    import os
    from cryptography.fernet import Fernet
    from django.conf import settings
    key = os.environ.get("MINEFOOT_MFA_KEY")
    if key:
        return Fernet(key.encode())
    # Development fallback: derived from SECRET_KEY. Set MINEFOOT_MFA_KEY in production.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("mfa:" + settings.SECRET_KEY).encode()).digest()))


def set_mfa_secret(user, secret: str):
    user.mfa_secret_encrypted = _fernet().encrypt(secret.encode()).decode()


def get_mfa_secret(user) -> str:
    return _fernet().decrypt(user.mfa_secret_encrypted.encode()).decode() if user.mfa_secret_encrypted else ""
