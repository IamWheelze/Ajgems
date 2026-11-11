"""Human decisions on detections (FR-091 to FR-093, FRD 9.1 Review and Evidence).

Reviews are append-only: a decision is never changed, only followed by a newer one.
The detection's status is always the result of its latest review.
"""
from django.conf import settings
from django.contrib.gis.db import models

from audit.models import AppendOnly

# Action -> (resulting detection status, roles allowed, statuses it may follow (None = any))
ACTIONS = {
    "confirm": ("confirmed", {"gis"}, None),
    "reject": ("rejected", {"gis"}, None),
    "edit": ("edited", {"gis"}, None),
    "uncertain": ("uncertain", {"gis"}, None),
    "request_field_check": ("field_check_requested", {"gis"}, None),
    "field_confirm": ("field_confirmed", {"field"}, {"field_check_requested"}),
    "field_reject": ("field_rejected", {"field"}, {"field_check_requested"}),
    "release": ("released", {"gis"}, {"confirmed", "edited", "field_confirmed"}),
}

# FR-091: every action needs a reason code. "other" also needs a note.
REASONS = {
    "confirm": ["visible_disturbance", "matches_reference_imagery", "other"],
    "reject": ["farmland", "settlement_or_construction", "riverbank_or_water", "bare_natural_ground",
               "quarry_or_borrow_pit", "burn_scar", "cloud_or_shadow", "imagery_artefact", "other"],
    "edit": ["boundary_too_large", "boundary_too_small", "merged_sites", "split_sites", "other"],
    "uncertain": ["ambiguous_signature", "insufficient_resolution", "possible_quarry", "other"],
    "request_field_check": ["needs_ground_truth", "high_impact", "conflicting_evidence", "other"],
    "field_confirm": ["observed_on_site", "operator_confirmed", "other"],
    "field_reject": ["not_observed_on_site", "different_land_use", "other"],
    "release": ["review_complete"],
}

EVIDENCE_GRADE = {"confirm": "E1", "edit": "E1", "field_confirm": "E3", "field_reject": "E3"}


class Review(AppendOnly):
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    detection = models.ForeignKey("projects.Detection", on_delete=models.PROTECT, related_name="reviews")
    action = models.CharField(max_length=30, choices=[(a, a) for a in ACTIONS])
    reason_code = models.CharField(max_length=40)
    note = models.TextField(blank=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewer_role = models.CharField(max_length=20)
    status_before = models.CharField(max_length=30)
    status_after = models.CharField(max_length=30)
    geometry_before = models.PolygonField(srid=4326, null=True)
    geometry_after = models.PolygonField(srid=4326, null=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]


class Evidence(AppendOnly):
    """Supporting evidence attached to a review (grade and source date required)."""
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    review = models.ForeignKey(Review, on_delete=models.PROTECT, related_name="evidence")
    kind = models.CharField(max_length=40, help_text="imagery, vhr_reference, field_photo, gps_track, note")
    grade = models.CharField(max_length=2, choices=[(g, g) for g in ("E0", "E1", "E2", "E3")])
    source_date = models.DateField()
    observer = models.CharField(max_length=200, blank=True)
    access_level = models.CharField(max_length=20, default="project")
    file = models.CharField(max_length=500, blank=True)
    notes = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)


MISSED_REASONS = ["not_detected", "only_partly_detected", "other"]


class MissedSite(AppendOnly):
    """A disturbance the model missed, added by an analyst (FR-093). Counted as a false negative,
    separately from candidates. Like reviews, it is never changed or deleted, and it reaches
    ground truth only through independent QC (FR-075)."""
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="missed_sites")
    job = models.ForeignKey("projects.Job", on_delete=models.PROTECT, null=True, related_name="missed_sites",
                            help_text="the finished job whose area it lies in")
    geometry = models.PolygonField(srid=4326)
    area_ha = models.FloatField()
    reason_code = models.CharField(max_length=40)
    note = models.TextField(blank=True)
    tile_outcome = models.CharField(max_length=40, blank=True,
                                    help_text="outcome of the job tile at its centre: a miss in a tile without usable "
                                              "imagery is not a model miss (FR-052)")
    scene_date = models.DateField(null=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    added_by_role = models.CharField(max_length=20)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]


# FR-075: the only path from reviewed output to training labels. A reviewer nominates; an independent person checks.
POSITIVE_FROM = {"confirmed", "edited", "field_confirmed"}
NEGATIVE_FROM = {"rejected", "field_rejected"}
# Reject reasons that name a hard-negative type (FR-074). Others (cloud, artefact, "other") are not used as negatives.
NEGATIVE_TYPE = {"farmland": "farmland", "settlement_or_construction": "settlement", "riverbank_or_water": "riverbank",
                 "bare_natural_ground": "bare_ground", "quarry_or_borrow_pit": "quarry_or_borrow_pit", "burn_scar": "burn_scar",
                 "different_land_use": "other_land_use", "not_observed_on_site": "other_land_use"}
QC_DECISIONS = {"accept": ["matches_imagery", "outline_adequate", "other"],
                "return": ["outline_inaccurate", "not_visible_in_imagery", "wrong_class", "duplicate", "other"]}


class Nomination(AppendOnly):
    """A reviewed detection or missed site proposed as a training example. Never a label by itself."""
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="nominations")
    detection = models.ForeignKey("projects.Detection", on_delete=models.PROTECT, null=True, related_name="nominations")
    missed_site = models.ForeignKey(MissedSite, on_delete=models.PROTECT, null=True, related_name="nominations")
    kind = models.CharField(max_length=20, choices=[("mining", "mining"), ("hard_negative", "hard negative")])
    negative_type = models.CharField(max_length=40, blank=True)
    geometry = models.PolygonField(srid=4326, help_text="snapshot at nomination")
    scene_id = models.CharField(max_length=120)
    scene_date = models.DateField(null=True)
    status_at_nomination = models.CharField(max_length=30)
    protocol_version = models.CharField(max_length=20, default="0.1")
    nominated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    note = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]


class QCDecision(AppendOnly):
    """An independent check of a nomination. The latest decision is the nomination's state."""
    organisation = models.ForeignKey("accounts.Organisation", on_delete=models.PROTECT)
    nomination = models.ForeignKey(Nomination, on_delete=models.PROTECT, related_name="decisions")
    decision = models.CharField(max_length=10, choices=[(d, d) for d in QC_DECISIONS])
    reason_code = models.CharField(max_length=40)
    note = models.TextField(blank=True)
    checker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    checker_role = models.CharField(max_length=20)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]
