from rest_framework import serializers

from .models import AreaOfInterest, Detection, Project, Scene


class ProjectSerializer(serializers.ModelSerializer):
    acceptable_use = serializers.BooleanField(write_only=True, required=False)
    areas = serializers.SerializerMethodField()
    detections = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ["id", "name", "purpose", "status", "created_at", "acceptable_use", "acceptable_use_accepted_at",
                  "areas", "detections"]
        read_only_fields = ["status", "created_at", "acceptable_use_accepted_at"]

    def get_areas(self, p):
        return p.areas.count()

    def get_detections(self, p):
        return p.detections.count()

    def create(self, validated_data):
        validated_data.pop("acceptable_use", None)   # recorded as who accepted and when, not as a field
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("acceptable_use", None)
        return super().update(instance, validated_data)

    def validate(self, data):
        if self.instance is None and not data.get("acceptable_use"):
            raise serializers.ValidationError({"acceptable_use": "accept the acceptable-use terms to create a project"})
        if not (data.get("purpose") or getattr(self.instance, "purpose", "")).strip():
            raise serializers.ValidationError({"purpose": "state the project's purpose"})
        return data


class AreaSerializer(serializers.ModelSerializer):
    geometry = serializers.SerializerMethodField()

    class Meta:
        model = AreaOfInterest
        fields = ["id", "name", "area_km2", "source", "source_crs", "created_at", "geometry"]

    def get_geometry(self, a):
        import json
        return json.loads(a.geometry.geojson)


class SceneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scene
        fields = ["id", "source_id", "provider", "sensor", "acquired_at", "processing_baseline", "cloud_pct",
                  "licence_note"]


SCORE_STATEMENT = ("Candidate footprint of visible land disturbance resembling surface mining. It does not establish "
                   "mineral type, ownership, legality or current activity. Scores are model scores, not probabilities.")


def detection_feature(d, include_history=False):
    import json
    props = {
        "id": d.id, "project_id": d.project_id, "status": d.status, "area_ha": round(d.area_ha, 2),
        "score_band": d.score_band, "score_type": d.score_type, "threshold": d.threshold,
        "min_mapping_unit_ha": d.min_mapping_unit_ha, "model_version": d.model_version,
        "input_schema_version": d.input_schema_version, "masked_fraction": round(d.masked_fraction, 3),
        "mask_status": d.mask_status, "evidence_grade": d.evidence_grade,
        "scene": SceneSerializer(d.scene).data, "statement": SCORE_STATEMENT,
    }
    if include_history:
        props["reviews"] = [{"action": r.action, "reason_code": r.reason_code, "note": r.note,
                             "reviewer_role": r.reviewer_role, "status_before": r.status_before,
                             "status_after": r.status_after, "at": r.at.isoformat(),
                             "geometry_before": json.loads(r.geometry_before.geojson) if r.geometry_before else None}
                            for r in d.reviews.all()]
        props["original_geometry"] = json.loads(d.original_geometry.geojson)
    return {"type": "Feature", "id": d.id, "geometry": json.loads(d.geometry.geojson), "properties": props}
