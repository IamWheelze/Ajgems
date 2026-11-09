"""Model registry (FR-101 to FR-104): lineage, lifecycle states, comparison reports, two-person approval."""
from django.conf import settings
from django.db import models


class ModelVersion(models.Model):
    class State(models.TextChoices):   # FRD 9.4
        EXPERIMENTAL = "experimental"; CANDIDATE = "candidate"; UNDER_EVALUATION = "under_evaluation"
        APPROVED_FOR_PILOT = "approved_for_pilot"; PRODUCTION = "production"; ROLLED_BACK = "rolled_back"
        RETIRED = "retired"

    name = models.CharField(max_length=80, unique=True)
    run_path = models.CharField(max_length=500)
    state = models.CharField(max_length=30, choices=State.choices, default=State.EXPERIMENTAL)
    input_schema = models.CharField(max_length=40)
    code_commit = models.CharField(max_length=80)
    dataset = models.JSONField(help_text="name, version, licence, label layer")
    licences = models.JSONField(help_text="every licence in the model's lineage (FR-071)")
    normalisation = models.JSONField()
    threshold = models.FloatField()
    metrics = models.JSONField(help_text="validation and test summaries from the run")
    card = models.JSONField(help_text="intended use, domain, limits (FRD 8.6)")
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    registered_at = models.DateTimeField(auto_now_add=True)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    proposed_at = models.DateTimeField(null=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    approved_at = models.DateTimeField(null=True)
    previous_production = models.ForeignKey("self", on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        ordering = ["-registered_at"]


class ComparisonReport(models.Model):
    """FR-103: a candidate against the current production model on the same locked sites."""
    candidate = models.ForeignKey(ModelVersion, on_delete=models.PROTECT, related_name="reports")
    baseline = models.ForeignKey(ModelVersion, on_delete=models.PROTECT, null=True, related_name="+")
    split = models.CharField(max_length=10)
    sites = models.IntegerField()
    result = models.JSONField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
