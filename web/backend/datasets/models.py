"""Dataset versions and samples for the dataset explorer (FR-071, FR-131, FRD 9.1 Dataset/Sample)."""
from django.contrib.gis.db import models


class DatasetVersion(models.Model):
    class State(models.TextChoices):   # FRD 9.4
        PROPOSED = "proposed"; LICENCE_REVIEW = "licence_review"; INGESTED = "ingested"; VALIDATED = "validated"
        FROZEN = "frozen"; DEPRECATED = "deprecated"; WITHDRAWN = "withdrawn"

    name = models.CharField(max_length=80)
    version = models.CharField(max_length=40)
    licence = models.CharField(max_length=80)
    licence_status = models.CharField(max_length=200, help_text="From docs/licence_register.csv")
    source = models.CharField(max_length=300)
    state = models.CharField(max_length=20, choices=State.choices, default=State.INGESTED)
    split_name = models.CharField(max_length=80)
    split_sha256 = models.CharField(max_length=64)
    test_lock_sha256 = models.CharField(max_length=64, blank=True)
    split_check = models.JSONField(default=dict, help_text="minefoot check-split result at import")
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("name", "version", "split_name")]


class Sample(models.Model):
    dataset = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="samples")
    site_id = models.CharField(max_length=60)
    split = models.CharField(max_length=40)
    source_split = models.CharField(max_length=20, blank=True)
    unit_id = models.CharField(max_length=20, blank=True)
    block_id = models.CharField(max_length=60, blank=True)
    mine_type = models.CharField(max_length=60, blank=True)
    mine_scale = models.CharField(max_length=40, blank=True)
    region = models.CharField(max_length=40, blank=True)
    scene_id = models.CharField(max_length=120, blank=True)
    acquired = models.DateField(null=True)
    footprint = models.PolygonField(srid=4326)
    labels = models.MultiPolygonField(srid=4326, null=True)
    locked_test = models.BooleanField(default=False)

    class Meta:
        unique_together = [("dataset", "site_id")]


class Overlap(models.Model):
    dataset = models.ForeignKey(DatasetVersion, on_delete=models.CASCADE, related_name="overlaps")
    site_a = models.CharField(max_length=60)
    site_b = models.CharField(max_length=60)
    kind = models.CharField(max_length=30)
    cross_split = models.BooleanField()
    iou = models.FloatField(null=True)
