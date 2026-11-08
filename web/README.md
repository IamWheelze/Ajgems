# minefoot web app (slice W1)

Django + GeoDjango + Django REST Framework + PostGIS backend, React + MapLibre frontend (FRD section 11). Raster work stays in the `minefoot` package.

## Run it locally

You need Python 3.10+, Node 20+, and Docker (for PostGIS and Redis) or a local PostgreSQL with PostGIS. GeoDjango also needs the GDAL and GEOS system libraries (Ubuntu: `apt install gdal-bin libgeos-dev`; macOS: `brew install gdal geos`). Use GDAL 3.9 or later in production; the system check warns on older versions.

```bash
docker compose -f web/docker-compose.yml up -d
pip install -e ".[g2,web,dev]"
cd web/backend && python manage.py migrate && python manage.py createsuperuser
python manage.py runserver 8000
```

In a second terminal:

```bash
cd web/frontend && npm install && npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to Django, so sign-in cookies work.

Users, organisations and roles are managed in the Django admin at http://localhost:8000/admin/. Set `organisation` and `roles` (a list such as `["gis"]`) on each user.

### Demo data (internal research)

With a finished G2 run and its prepared sites on disk:

```bash
cd web/backend && python manage.py load_demo --run ../../data/runs/g2_hn --password 'choose-a-password' --sites 20
```

This creates the organisation "Internal research (demo)", users `demo_admin`, `demo_gis`, `demo_field`, `demo_viewer` and `demo_ml`, and one project with the run's candidate polygons on 20 Global ML-ready test sites. They are not Nigerian results.

### Tests

```bash
cd web/backend && python manage.py test
```

## What W1 covers

- **Roles and isolation.** Every endpoint checks roles (FR-002). Other organisations' objects return 404 (FR-003). ML administrators cannot see customer detections; viewers see released detections only.
- **Projects** need a stated purpose and acceptance of the acceptable-use terms. Projects are archived, never deleted, and archived projects are read-only (FR-010).
- **Areas** can be drawn on the map or uploaded as GeoJSON, KML, zipped Shapefile or GeoPackage. Refusals name the reason (self-intersection, no CRS, too large); area is geodesic, within 0.1%; the limit is 10,000 km² (FR-011 to FR-013).
- **Review workspace** (FRD 10.4):
  - *Queue:* by status, with progress.
  - *Map:* the candidate's own dated Sentinel-2 scene with band presets.
  - *Evidence:* area, score band (never a raw score), masked share "not assessed", model, settings, the standard limitation statement and history.
  - *Decisions:* need a reason code, and are limited by role and status (FR-091).
  - *Keyboard:* `c`/`r`/`u`/`f`, `j`/`k`, Enter.
- **Append-only history.** Reviews, evidence and the audit log cannot be changed or deleted, even with raw SQL, because database triggers refuse it (FR-092). Sign-ins, failed sign-ins and every change are audited (FR-004).
- **Status styling.** Colour-blind-safe colours, always with an outline style and an icon (FRD 10.3).

## Not built yet (next slices)

- **W2, jobs:** run detection on a project area through Celery workers.
  - *Steps:* imagery search and quality check, the FR-052 tile outcomes, scoring with the approved model, then `minefoot.vectorise` with the masking report.
  - *Screens:* the job monitor and run setup.
- **W3, review and display:**
  - *Map display:* a "not assessed" hatch layer, swipe, and geometry editing in the review workspace.
  - *Delivery:* exports (GeoJSON, GeoPackage, CSV, PDF report with licence notice, FR-106 to FR-108).
  - *Serving:* a raster tile server (TiTiler) instead of whole-scene PNGs.
- **Security:** multi-factor sign-in for privileged roles (FR-001). The interface flags privileged users until it exists.
- **Model registry** with second-person approval (FR-102 to FR-104), and the dataset explorer.
