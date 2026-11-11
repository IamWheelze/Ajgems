from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts.views import CsrfView, LoginView, LogoutView, MeView, MfaConfirmView, MfaSetupView, MfaVerifyView
from audit.views import AuditListView
from projects.views import AreaViewSet, DetectionImageView, DetectionViewSet, DetectionDatesView, EditGeometryView, ExportView, PixelView, RestrictView, RestrictedGrantView, AreaDomainView, JobViewSet, ModelCardView, ProjectViewSet, SceneImageView
from datasets.views import DatasetListView, SampleDetailView, SampleListView
from registry.views import ErrorImageView, ErrorSitesView, ModelActionView, ModelDashboardView, ModelListView
from review.views import MissedSiteView, PoolDecideView, PoolExportView, PoolImageView, PoolView, ReviewCreateView, ReviewOptionsView

# FR-001: the Django admin is for administrators, so it also requires a session whose second factor was
# verified through the app's sign-in (/api/auth/login/ then /api/auth/mfa/verify/).
_default_has_permission = admin.site.has_permission
admin.site.has_permission = lambda request: (_default_has_permission(request)
                                             and request.session.get("mfa_verified") is True)

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")

areas = AreaViewSet.as_view({"get": "list", "post": "create"})
detections = DetectionViewSet.as_view({"get": "list"})
detection = DetectionViewSet.as_view({"get": "retrieve"})
jobs = JobViewSet.as_view({"get": "list", "post": "create"})
job = JobViewSet.as_view({"get": "retrieve"})
job_tiles = JobViewSet.as_view({"get": "tiles"})
job_start = JobViewSet.as_view({"post": "start"})
job_preview = JobViewSet.as_view({"get": "preview"})
job_rethreshold = JobViewSet.as_view({"post": "rethreshold"})
job_cancel = JobViewSet.as_view({"post": "cancel"})
job_resume = JobViewSet.as_view({"post": "resume"})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/csrf/", CsrfView.as_view()),
    path("api/auth/login/", LoginView.as_view()),
    path("api/auth/logout/", LogoutView.as_view()),
    path("api/auth/mfa/verify/", MfaVerifyView.as_view()),
    path("api/auth/mfa/setup/", MfaSetupView.as_view()),
    path("api/auth/mfa/confirm/", MfaConfirmView.as_view()),
    path("api/me/", MeView.as_view()),
    path("api/", include(router.urls)),
    path("api/projects/<int:project_pk>/areas/", areas),
    path("api/projects/<int:project_pk>/detections/", detections),
    path("api/detections/<int:pk>/", detection),
    path("api/detections/<int:pk>/reviews/", ReviewCreateView.as_view()),
    path("api/detections/<int:pk>/image/", DetectionImageView.as_view()),
    path("api/detections/<int:pk>/edit-geometry/", EditGeometryView.as_view()),
    path("api/detections/<int:pk>/pixel/", PixelView.as_view()),
    path("api/detections/<int:pk>/dates/", DetectionDatesView.as_view()),
    path("api/detections/<int:pk>/restrict/", RestrictView.as_view()),
    path("api/areas/<int:pk>/domain/", AreaDomainView.as_view()),
    path("api/projects/<int:project_pk>/restricted-grants/", RestrictedGrantView.as_view()),
    path("api/review-options/", ReviewOptionsView.as_view()),
    path("api/projects/<int:project_pk>/missed-sites/", MissedSiteView.as_view()),
    path("api/pool/", PoolView.as_view()),
    path("api/pool/export/", PoolExportView.as_view()),
    path("api/pool/<int:pk>/decide/", PoolDecideView.as_view()),
    path("api/pool/<int:pk>/image/", PoolImageView.as_view()),
    path("api/scenes/<int:pk>/image/", SceneImageView.as_view()),
    path("api/audit/", AuditListView.as_view()),
    path("api/model-card/", ModelCardView.as_view()),
    path("api/models/", ModelListView.as_view()),
    path("api/datasets/", DatasetListView.as_view()),
    path("api/datasets/<int:pk>/samples/", SampleListView.as_view()),
    path("api/samples/<int:pk>/", SampleDetailView.as_view()),
    path("api/models/<int:pk>/dashboard/", ModelDashboardView.as_view()),
    path("api/models/<int:pk>/errors/", ErrorSitesView.as_view()),
    path("api/models/<int:pk>/errors/<str:site_id>/image/", ErrorImageView.as_view()),
    path("api/models/<int:pk>/<str:action>/", ModelActionView.as_view()),
    path("api/projects/<int:project_pk>/exports/", ExportView.as_view()),
    path("api/projects/<int:project_pk>/jobs/", jobs),
    path("api/jobs/<int:pk>/", job),
    path("api/jobs/<int:pk>/tiles/", job_tiles),
    path("api/jobs/<int:pk>/start/", job_start),
    path("api/jobs/<int:pk>/preview/", job_preview),
    path("api/jobs/<int:pk>/rethreshold/", job_rethreshold),
    path("api/jobs/<int:pk>/cancel/", job_cancel),
    path("api/jobs/<int:pk>/resume/", job_resume),
]
