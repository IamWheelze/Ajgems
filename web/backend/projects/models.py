"""Projects, areas, imagery, jobs, tiles and detections (FRD 9.1, 9.4).

What was observed (Scene, Tile), what the model said (Detection) and what a person
decided (review.Review) are kept in separate tables.
"""
from django.conf import settings
from django.contrib.gis.db import models


class Project(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT, related_name="projects")
    name = models.CharField(max_length=200)
    purpose = models.TextField()
    acceptable_use_accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                                   related_name="+")
    acceptable_use_accepted_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Use(models.TextChoices):   # FR-025: what the project's outputs are for; sources must be approved for it
        INTERNAL_RESEARCH = "internal_research"; EVALUATION = "evaluation"; COMMERCIAL = "commercial"

    intended_use = models.CharField(max_length=20, choices=Use.choices, default=Use.INTERNAL_RESEARCH)

    class Meta:
        unique_together = [("organisation", "name")]
        ordering = ["-created_at"]


class SourceApproval(models.Model):
    """FR-025: an imagery source approved for one intended use, with the licence evidence. Administrators keep
    these in the Django admin; jobs search only approved sources and refuse with the reason when none is."""
    provider = models.CharField(max_length=40)
    use = models.CharField(max_length=20, choices=Project.Use.choices)
    licence_evidence = models.TextField(help_text="what the licence register says, and where")
    approved = models.BooleanField(default=True)
    decided_by = models.CharField(max_length=150, help_text="person or record that made this decision")
    decided_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("provider", "use")]


class AreaOfInterest(models.Model):
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="areas")
    name = models.CharField(max_length=200)
    geometry = models.MultiPolygonField(srid=4326)
    area_km2 = models.FloatField()
    source = models.CharField(max_length=40, help_text="drawn, coordinates, or the uploaded file type")
    source_crs = models.CharField(max_length=40)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class Scene(models.Model):
    """One satellite product as retrieved, with provenance (FR-023)."""
    source_id = models.CharField(max_length=120)
    provider = models.CharField(max_length=40)
    sensor = models.CharField(max_length=40)
    acquired_at = models.DateTimeField()
    processing_baseline = models.CharField(max_length=10)
    cloud_pct = models.FloatField(null=True)
    licence_note = models.CharField(max_length=200)
    retrieved_at = models.DateTimeField(null=True, help_text="when this product was first fetched (FR-023)")
    footprint = models.PolygonField(srid=4326, null=True)
    prepared_path = models.CharField(max_length=500, blank=True, help_text="minefoot prepared folder on the server")

    class Meta:
        unique_together = [("provider", "source_id")]


class Job(models.Model):
    class State(models.TextChoices):   # FRD 9.4
        DRAFT = "draft"; ESTIMATED = "estimated"; QUEUED = "queued"; FETCHING = "fetching"
        CHECKING = "checking"; RUNNING = "running"; VECTORISING = "vectorising"; SUCCEEDED = "succeeded"
        SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"; BLOCKED_BY_QUALITY = "blocked_by_quality"
        FAILED = "failed"; CANCELLING = "cancelling"; CANCELLED = "cancelled"; RESUMABLE = "resumable"

    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="jobs")
    area = models.ForeignKey(AreaOfInterest, on_delete=models.PROTECT, null=True)
    kind = models.CharField(max_length=30, default="detection")
    state = models.CharField(max_length=30, choices=State.choices, default=State.DRAFT)
    model_version = models.CharField(max_length=80)
    parameters = models.JSONField(default=dict, help_text="Immutable once the job starts")
    scenes = models.ManyToManyField(Scene, blank=True)
    failure_reason = models.CharField(max_length=80, blank=True)
    estimate = models.JSONField(default=dict, help_text="Tiles, scenes and time estimated before starting")
    actual = models.JSONField(default=dict, blank=True, help_text="FR-086: measured time, storage and cost at the end")
    message = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)


class Tile(models.Model):
    class Outcome(models.TextChoices):   # FR-052: exactly four outcomes
        CANDIDATE_FOUND = "candidate_found"
        NO_CANDIDATE = "no_candidate_found_in_usable_imagery"
        INSUFFICIENT = "insufficient_usable_imagery"
        NOT_PROCESSED = "not_processed"

    class State(models.TextChoices):
        PENDING = "pending"; FETCHING = "fetching"; CHECKING = "checking"; RUNNING = "running"
        DONE = "done"; FAILED = "failed"; SKIPPED = "skipped"

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="tiles")
    grid_id = models.CharField(max_length=60)
    geometry = models.PolygonField(srid=4326)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    scene = models.ForeignKey(Scene, on_delete=models.PROTECT, null=True, help_text="Source of every pixel in the tile")
    scene_cover = models.FloatField(null=True, help_text="Share of the tile the scene covers")
    usable_pct = models.FloatField(null=True)
    quality_decision = models.CharField(max_length=10, blank=True, help_text="pass, warn or block (FR-053)")
    candidates = models.IntegerField(default=0)
    failure_reason = models.CharField(max_length=80, blank=True)
    prepared_path = models.CharField(max_length=500, blank=True, help_text="minefoot prepared folder for this tile")
    score_path = models.CharField(max_length=500, blank=True, help_text="the tile's model-score map (uint8 levels)")
    checksums = models.JSONField(default=dict, blank=True, help_text="SHA-256 of the fetched and prepared rasters (FR-021)")
    bytes_fetched = models.BigIntegerField(null=True, help_text="size of the windowed download")
    outcome = models.CharField(max_length=40, choices=Outcome.choices, default=Outcome.NOT_PROCESSED)

    class Meta:
        unique_together = [("job", "grid_id")]


class Detection(models.Model):
    """A candidate footprint: what the model said. Never "mine", "illegal" or a mineral (FR-110)."""

    class Status(models.TextChoices):   # FRD 9.4
        UNREVIEWED = "unreviewed"; CONFIRMED = "confirmed"; REJECTED = "rejected"; EDITED = "edited"
        UNCERTAIN = "uncertain"; FIELD_CHECK_REQUESTED = "field_check_requested"
        FIELD_CONFIRMED = "field_confirmed"; FIELD_REJECTED = "field_rejected"; RELEASED = "released"

    class ScoreType(models.TextChoices):   # FR-084
        MODEL_SCORE = "model score"
        CALIBRATED = "calibrated probability"

    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="detections")
    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="detections")
    scene = models.ForeignKey(Scene, on_delete=models.PROTECT)
    geometry = models.PolygonField(srid=4326, help_text="Current geometry; edits keep both in review history")
    original_geometry = models.PolygonField(srid=4326)
    area_ha = models.FloatField()
    score_mean = models.FloatField()
    score_band = models.CharField(max_length=10)
    score_type = models.CharField(max_length=30, choices=ScoreType.choices, default=ScoreType.MODEL_SCORE)
    threshold = models.FloatField()
    min_mapping_unit_ha = models.FloatField()
    model_version = models.CharField(max_length=80)
    input_schema_version = models.CharField(max_length=40)
    masked_fraction = models.FloatField(help_text="Share of the polygon the quality mask removed (FR-052)")
    mask_status = models.CharField(max_length=20)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNREVIEWED)
    evidence_grade = models.CharField(max_length=2, default="E0")
    restricted = models.BooleanField(default=False, help_text="FR-005: location withheld from viewers and exports without a grant")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-score_mean", "id"]


class RestrictedGrant(models.Model):
    """FR-005: explicit permission for one user to see and export a project's restricted sites."""
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="restricted_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField()
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True)

    @staticmethod
    def has(user, project) -> bool:
        return RestrictedGrant.objects.filter(project=project, user=user, revoked_at=None).exists()


class Upload(models.Model):
    """FR-022: a user-supplied Sentinel-2 L2A product, received in verified chunks and resumable at any byte."""
    class State(models.TextChoices):
        RECEIVING = "receiving"; READY = "ready"; FAILED = "failed"

    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="uploads")
    filename = models.CharField(max_length=255)
    size = models.BigIntegerField()
    received = models.BigIntegerField(default=0)
    state = models.CharField(max_length=20, choices=State.choices, default=State.RECEIVING)
    path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64, blank=True, help_text="of the whole file, computed on completion")
    meta = models.JSONField(default=dict, blank=True, help_text="product metadata read from MTD_MSIL2A.xml")
    footprint = models.PolygonField(srid=4326, null=True)
    acquired_at = models.DateTimeField(null=True)
    cloud_pct = models.FloatField(null=True)
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
