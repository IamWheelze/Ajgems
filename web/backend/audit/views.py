from rest_framework import serializers
from rest_framework.generics import ListAPIView

from accounts.permissions import OrgScopedMixin, RolePermission

from .models import AuditEvent


class AuditSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.username", default=None)

    class Meta:
        model = AuditEvent
        fields = ["id", "actor", "action", "object_type", "object_id", "before", "after", "at"]


class AuditListView(OrgScopedMixin, ListAPIView):
    """Administrators search the audit log (FR-004). Read-only: the log is append-only."""
    queryset = AuditEvent.objects.select_related("actor")
    serializer_class = AuditSerializer
    permission_classes = [RolePermission]
    read_roles = ("admin",)

    def get_queryset(self):
        qs = super().get_queryset()
        for key in ("action", "object_type", "object_id"):
            if self.request.query_params.get(key):
                qs = qs.filter(**{key: self.request.query_params[key]})
        return qs
