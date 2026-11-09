from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts.views import CsrfView, LoginView, LogoutView, MeView
from audit.views import AuditListView
from projects.views import AreaViewSet, DetectionImageView, DetectionViewSet, EditGeometryView, ExportView, JobViewSet, ModelCardView, ProjectViewSet, SceneImageView
from review.views import ReviewCreateView, ReviewOptionsView

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")

areas = AreaViewSet.as_view({"get": "list", "post": "create"})
detections = DetectionViewSet.as_view({"get": "list"})
detection = DetectionViewSet.as_view({"get": "retrieve"})
jobs = JobViewSet.as_view({"get": "list", "post": "create"})
job = JobViewSet.as_view({"get": "retrieve"})
job_tiles = JobViewSet.as_view({"get": "tiles"})
job_start = JobViewSet.as_view({"post": "start"})
job_cancel = JobViewSet.as_view({"post": "cancel"})
job_resume = JobViewSet.as_view({"post": "resume"})

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/csrf/", CsrfView.as_view()),
    path("api/auth/login/", LoginView.as_view()),
    path("api/auth/logout/", LogoutView.as_view()),
    path("api/me/", MeView.as_view()),
    path("api/", include(router.urls)),
    path("api/projects/<int:project_pk>/areas/", areas),
    path("api/projects/<int:project_pk>/detections/", detections),
    path("api/detections/<int:pk>/", detection),
    path("api/detections/<int:pk>/reviews/", ReviewCreateView.as_view()),
    path("api/detections/<int:pk>/image/", DetectionImageView.as_view()),
    path("api/detections/<int:pk>/edit-geometry/", EditGeometryView.as_view()),
    path("api/review-options/", ReviewOptionsView.as_view()),
    path("api/scenes/<int:pk>/image/", SceneImageView.as_view()),
    path("api/audit/", AuditListView.as_view()),
    path("api/model-card/", ModelCardView.as_view()),
    path("api/projects/<int:project_pk>/exports/", ExportView.as_view()),
    path("api/projects/<int:project_pk>/jobs/", jobs),
    path("api/jobs/<int:pk>/", job),
    path("api/jobs/<int:pk>/tiles/", job_tiles),
    path("api/jobs/<int:pk>/start/", job_start),
    path("api/jobs/<int:pk>/cancel/", job_cancel),
    path("api/jobs/<int:pk>/resume/", job_resume),
]
