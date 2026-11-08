"""Append-only audit log (FR-004, FR-092, FRD 9.1 AuditEvent).

Rows can be added, never changed or removed: the model refuses it, and a database
trigger (migration 0002) refuses UPDATE and DELETE even from raw SQL.
"""
from django.conf import settings
from django.db import models


class AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise PermissionError(f"{type(self).__name__} is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(f"{type(self).__name__} is append-only")


class AuditEvent(AppendOnly):
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT, null=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=60)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]


def record(actor, action, obj=None, before=None, after=None, organisation=None):
    """Append one audit event."""
    org = organisation or getattr(obj, "organisation", None) or getattr(actor, "organisation", None)
    return AuditEvent.objects.create(
        organisation=org, actor=actor if getattr(actor, "pk", None) else None, action=action,
        object_type=type(obj).__name__ if obj is not None else "", object_id=str(getattr(obj, "pk", "") or ""),
        before=before, after=after)
