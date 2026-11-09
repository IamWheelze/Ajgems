"""Dataset explorer API (FR-131): site map by split, filters, class balance, label overlay, overlap warnings."""
import json
from collections import Counter

from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RolePermission

from .models import DatasetVersion, Sample

FILTERS = ("split", "mine_scale", "mine_type", "region")


class DatasetListView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request):
        out = []
        for d in DatasetVersion.objects.all().order_by("name", "version"):
            ov = d.overlaps.all()
            out.append({"id": d.id, "name": d.name, "version": d.version, "state": d.state, "licence": d.licence,
                        "licence_status": d.licence_status, "source": d.source, "split_name": d.split_name,
                        "split_sha256": d.split_sha256, "test_lock_sha256": d.test_lock_sha256, "split_check": d.split_check,
                        "samples": d.samples.count(),
                        "overlaps": {"pairs": ov.count(), "cross_split": ov.filter(cross_split=True).count(),
                                     "by_kind": dict(Counter(ov.values_list("kind", flat=True)))}})
        return Response(out)


class SampleListView(APIView):
    """Footprints as GeoJSON (no labels: those come per site) with filters, plus class balance per split."""
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request, pk):
        try:
            d = DatasetVersion.objects.get(pk=pk)
        except DatasetVersion.DoesNotExist:
            raise Http404
        qs = d.samples.all()
        for f in FILTERS:
            if request.query_params.get(f):
                qs = qs.filter(**{f"{f}__in": request.query_params[f].split(",")})
        balance = {}
        for s in qs.values("split", "mine_scale", "mine_type", "region"):
            b = balance.setdefault(s["split"], {"sites": 0, "mine_scale": Counter(), "mine_type": Counter(), "region": Counter()})
            b["sites"] += 1
            for k in ("mine_scale", "mine_type", "region"):
                b[k][s[k]] += 1
        feats = [{"type": "Feature", "id": s.id, "geometry": json.loads(s.footprint.geojson),
                  "properties": {"id": s.id, "site_id": s.site_id, "split": s.split, "mine_scale": s.mine_scale,
                                 "mine_type": s.mine_type, "region": s.region, "locked_test": s.locked_test}} for s in qs]
        options = {f: sorted(set(d.samples.values_list(f, flat=True))) for f in FILTERS}
        return Response({"type": "FeatureCollection", "features": feats, "balance": balance, "options": options})


class SampleDetailView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("ml", "admin")

    def get(self, request, pk):
        try:
            s = Sample.objects.select_related("dataset").get(pk=pk)
        except Sample.DoesNotExist:
            raise Http404
        ov = s.dataset.overlaps.filter(site_a=s.site_id) | s.dataset.overlaps.filter(site_b=s.site_id)
        return Response({"site_id": s.site_id, "split": s.split, "source_split": s.source_split, "unit_id": s.unit_id,
                         "block_id": s.block_id, "mine_type": s.mine_type, "mine_scale": s.mine_scale, "region": s.region,
                         "scene_id": s.scene_id, "acquired": s.acquired, "locked_test": s.locked_test,
                         "footprint": json.loads(s.footprint.geojson),
                         "labels": json.loads(s.labels.geojson) if s.labels else None,
                         "overlaps": [{"other": o.site_b if o.site_a == s.site_id else o.site_a, "kind": o.kind,
                                       "cross_split": o.cross_split, "iou": o.iou} for o in ov]})
