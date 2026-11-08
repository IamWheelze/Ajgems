"""Role checks used by every API view (FR-002), and organisation scoping (FR-003).

Each view declares which roles may read and which may write. Objects from another
organisation are never found: views filter by request.user.organisation, so a
cross-organisation request by ID returns 404, not 403.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission


class RolePermission(BasePermission):
    """Grant access when the user holds one of the view's read_roles (GET) or write_roles (others)."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or user.organisation_id is None:
            return False
        roles = getattr(view, "read_roles" if request.method in SAFE_METHODS else "write_roles", ())
        action_roles = getattr(view, "action_roles", {}).get(getattr(view, "action", None))
        if action_roles is not None:
            roles = action_roles
        return user.has_role(*roles)


class OrgScopedMixin:
    """Limit a view's queryset to the requesting user's organisation."""
    org_field = "organisation"

    def get_queryset(self):
        return super().get_queryset().filter(**{self.org_field: self.request.user.organisation})
