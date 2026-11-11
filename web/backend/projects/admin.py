from django.contrib import admin

from .models import SourceApproval


@admin.register(SourceApproval)
class SourceApprovalAdmin(admin.ModelAdmin):
    """FR-025: administrators approve or withdraw imagery sources per intended use."""
    list_display = ("provider", "use", "approved", "decided_by", "decided_at")
    list_filter = ("use", "approved")
