"""Review endpoints (FR-091, FR-092): submit a decision, list the options."""
from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import RolePermission
from projects.models import Detection
from projects.serializers import detection_feature

from .models import ACTIONS, REASONS
from .services import ReviewError, submit_review


class ReviewCreateView(APIView):
    """POST {action, reason_code, note?, geometry?}. The detection's history is append-only."""
    permission_classes = [RolePermission]
    write_roles = ("gis", "field")

    def post(self, request, pk):
        try:
            submit_review(request.user, pk, request.data.get("action", ""), request.data.get("reason_code", ""),
                          request.data.get("note", "") or "", request.data.get("geometry"))
        except Detection.DoesNotExist:
            raise Http404
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=403)
        except ReviewError as exc:
            return Response({"detail": str(exc)}, status=400)
        d = Detection.objects.prefetch_related("reviews").select_related("scene").get(pk=pk)
        return Response(detection_feature(d, include_history=True), status=201)


class ReviewOptionsView(APIView):
    permission_classes = [RolePermission]
    read_roles = ("admin", "gis", "field", "viewer")

    def get(self, request):
        return Response({a: {"status_after": s, "roles": sorted(r), "allowed_from": sorted(f) if f else None,
                             "reasons": REASONS[a]} for a, (s, r, f) in ACTIONS.items()})
