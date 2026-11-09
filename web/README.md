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

In a second terminal, start a job worker (W2):

```bash
cd web/backend && celery -A minefoot_web worker -l info --concurrency 1
```

In a third terminal:

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

## What W2 covers (detection jobs)

- **Run setup** (on the project page). Choose an area, a date window and a cloud limit, then **Estimate**.
  - *What it does:* searches the catalogue and lays a 10 km tile grid. Nothing is downloaded yet.
  - *What it shows:* tile count, scenes found, estimated time (20 s per tile on CPU; 13 s measured), and the model card with its "not yet tested on Nigerian sites" warning until G3 has run.
  - *Then:* **Start job**.
- **Celery worker,** tile by tile (FR-080 to FR-085):
  1. *Scene:* the clearest scene that covers the tile, recorded per tile (FR-061).
  2. *Fetch:* a windowed fetch through the one preprocessing path.
  3. *Quality check:* a tile below 40% usable is **insufficient usable imagery**, and is never scored or called "no candidate" (FR-052).
  4. *Score and vectorise:* the model scores the tile; `minefoot.vectorise` produces polygons with the masking report.
  5. *Merge:* candidates crossing tile edges are merged and clipped to the area.
  - *Controls:* cancel stops before the next tile, and resume skips finished tiles. Failures carry FRD reasons, and a job's parameters are frozen once it starts.
- **Job monitor.** Tiles coloured by their FR-052 outcome, with "insufficient usable imagery" hatched. It polls live, shows tile details on click, offers cancel and resume, and links to review.
- **Embargo (ML-07).** Jobs overlapping the Jos Plateau and Kogi/Kwara labelling regions are refused until `docs/evidence/nigeria_test_lock_v1.json` exists, so model output cannot influence the Nigerian test labels.
- **Model.** Set by `MINEFOOT_MODEL_RUN` (default `data/runs/g2_hn`) until the model registry exists.

Verified: 4 job tests on synthetic imagery cover:
- FR-052 on a fully clouded tile,
- candidates merged across tile edges,
- cancel, then resume without refetching finished tiles,
- frozen parameters, the embargo, and roles.

Also run for real here: a 122 km² area in Ghana, 4 tiles, 18 candidates in 42–51 seconds on CPU, driven from the browser.

## What W3 covers (exports and review tools)

- **Export packages** (FR-106 to FR-109). One zip per request:
  - *Data:* GeoJSON, GeoPackage or CSV (WKT geometry), with the FRD 9.2 fields.
  - *PDF report:* the coverage map and the limits section are always included, along with method, model card, versions and evidence grades.
  - *Licence notice:* the Copernicus credit for each scene year.
  - *Manifest:* a SHA-256 for every file. Signing is not built yet.
  - *Which candidates:* the latest finished job per area by default, so reruns are not duplicated. Viewers export released candidates only.
- **"Not assessed" layer** (FR-125). In review, pixels the quality mask removed are hatched over the imagery, on by default.
- **Imagery under each candidate.** A mosaic of the job tiles it touches, not a single tile.
- **Boundary editing** (FR-091). The server returns a valid outline of at most 200 vertices, simplified with topology preserved. You drag vertices and record the edit with a reason. Invalid outlines are refused with the reason, and the original is kept in the history.
- **Smoother polygons.** `minefoot.vectorise` removes pixel stair-steps at 0.75 pixel; polygons stay valid and area changes by under 1%.

## Multi-factor sign-in (FR-001)

- **Who needs it.** Administrators and ML administrators use a 6-digit code from an authenticator app after their password.
- **Until they enrol:** every endpoint refuses them except sign-in, `/api/me` and enrolment. The app opens an enrolment screen with a QR code.
- **After enrolling:** the password alone does not sign them in. Five wrong codes end the attempt.
- **Secrets** are encrypted at rest with Fernet. Set `MINEFOOT_MFA_KEY` in production; generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Without it, a key derived from `DJANGO_SECRET_KEY` is used, which suits development only.
- **Django admin** at `/admin/` needs a session whose code was verified. Sign in through the app first, then open the admin in the same browser.
- **Audit:** enrolment, successful codes, wrong codes and locked attempts are all recorded.

## Model registry (FR-101 to FR-104)

- **Registration** of a finished G2 run (`python manage.py register_model --run ../../data/runs/<run> --user <ml user>`, or the API) is refused if any lineage field is missing: code commit (not uncommitted code), dataset, input schema, split hash, sites, parameters, normalisation, metrics and weights. The model card lists every licence in the lineage, including unverified ones.
- **Compare** a model with the production model on the same locked test sites (paired site bootstrap). Promotion is impossible without this report.
- **Propose, then approve.** A model is proposed by ML and approved by a **different** person (ML or admin); self-approval is refused. Inference refuses a model whose input schema does not match.
- **Rollback** returns to the previous production model in one step. Every action is audited.
- **Which model jobs use.** Detection jobs use the production model and freeze it in the job's parameters. With no production model, they use `MINEFOOT_MODEL_RUN`, labelled "(unregistered)".
- **Current state.** `g2_v1` and `g2_hn` are registered. `g2_hn` is under evaluation; nothing is approved for production yet, because that is a two-person decision.

## Dataset explorer (FR-131, internal)

`python manage.py import_dataset --split <split.gpkg> --overlaps <overlaps.csv> --lock <test_lock.json> --name <name> --dataset-version <v>` imports a dataset version's sites, labels, split, overlaps and test lock. The import also does three things:
- reruns the split leakage check,
- takes the licence status from `docs/licence_register.csv`,
- sets the state to "licence review" when the register says legal review is needed.

The explorer page (ML and admin users) shows:
- *Map:* sites coloured by split, with locked test sites outlined thicker.
- *Filters:* split, mine scale, mine type and region.
- *Class balance:* per split.
- *Warnings:* overlaps across splits.
- *Per site:* labels and details.

## Swipe comparison (FR-127, MVP)

In review, **Swipe compare** puts a second band preset (for example SWIR false colour) on the right of a draggable divider; arrow keys move it too.
- **Synced:** the right pane copies the main map's position and zoom on every move.
- **Same overlays:** candidate outlines in their status styles and the "not assessed" hatch are drawn on both sides.
- **Same interactions:** clicks, keyboard review and boundary editing all go to the main map.

## Not built yet (next slices)

- **Side-by-side and blink comparison, and comparing dates** (FR-127, Pilot). Swipe between band presets of one scene is built.
- **A raster tile server** (TiTiler) instead of whole-scene PNGs.
- **Experiment dashboard** (FR-130).
- **Signed export manifests** (FR-109). Checksums exist; signatures do not.
