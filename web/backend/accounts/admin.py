from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Organisation, User

@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    """Quotas (FR-087) are edited here; empty means no limit."""
    list_display = ("name", "region", "quota_km2_per_month", "quota_jobs_per_month", "quota_storage_gb")


@admin.register(User)
class MinefootUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Organisation and roles", {"fields": ("organisation", "roles")}),)
    list_display = ("username", "organisation", "roles", "is_active")
