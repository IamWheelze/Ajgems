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

## Map layers in review (FR-120 to FR-124)

- **Band presets:** true colour, SWIR false colour, NIR false colour, NDVI and NDWI. Each uses a fixed, documented stretch, shown under the preset (FR-122).
- **Custom stretch:** an optional stretch for the colour presets, for this view only. It is labelled with a warning badge and never stored.
- **Quality mask:** scene classification of unusable pixels (no data, saturated, cloud shadow, cloud, cirrus, snow), with a key (FR-123).
- **Model-score heatmap:** the job's stored score map in one hue, light to dark (FR-124). Scores under 0.1 are transparent, and the key gives the job's threshold.
  - Score maps are kept per tile (`Tile.score_path`). The demo project's candidates have none, so the toggle says so.
- **Pixel inspector** (FR-128): click the map to see the job tile's 10 band reflectances (not resampled), NDVI, NDWI, the mask class and whether the pixel was usable, the model score with the threshold, and the source scene. A test checks 20 sampled points against the rasters.
- **Cursor coordinates** in WGS 84, plus the scale bar (FR-120). The layer panel collapses.

## Condition prompts: haze or smoke, burn scars, water (FR-054)

Each processed tile, and each candidate, gets review prompts from `minefoot.conditions`, using simple spectral rules on usable pixels.
- **The rules:**
  - haze or smoke: a high haze-optimised transform over vegetation;
  - burn scar: dark ground with low NIR and a negative burn ratio;
  - water: NDWI above 0.1.
- **What they do:** they are prompts only. They never mask pixels and never change a score. They appear in the review evidence panel ("Check: …") and on the job monitor's tile card.
- **Measured** (`minefoot conditions-eval`, 120 Global ML-ready validation and test sites, `docs/evidence/conditions_v1.json`):
  - water against ESA WorldCover water and wetland: precision 0.98, recall 0.63;
  - haze could not be evaluated: these scenes contain no thin-cirrus pixels to compare with;
  - burn scars have no labelled examples, so only the firing rate is known: 5.0% of usable pixels overall, 8.2% of labelled mining pixels and 4.8% elsewhere. Dark wet mine ground often looks burn-like, so treat this prompt as "look closer", never as a reason to reject.
- **Thresholds** are provisional. Tile prompts fire at 20% (haze), 10% (burn) and 2% (water) of usable pixels; candidate prompts at 30% of the polygon. With these, 20 and 11 of the 120 sites get burn and water prompts.

## Field visits, offline (FR-095)

**Field visits** (field role) is a phone page that works without a connection.
- **Before going out:** it saves the open field-check tasks on the phone; a service worker in the built app caches the page itself.
- **At the site:**
  - record position (GPS with accuracy), time, observer, notes and photos, plus an optional field confirm or reject with a reason;
  - before a photo is kept, drag boxes over people, faces or number plates and they are blurred on the phone.
  - Visits wait in IndexedDB and sync automatically when the phone is back online (retried every 20 s).
  - A visit the rules refuse stays on the phone with the reason.
- **On the server:**
  - sync is idempotent by the visit's UUID, so retries never duplicate a visit;
  - photos are re-encoded with no EXIF (location, device serial);
  - visits and photos are append-only;
  - a field decision goes through the normal review rules (evidence grade E3).
  - Photos open only for field, GIS and admin users of the same organisation, and appear in the review evidence panel.
- **Tested:** the production build in a phone-sized browser. Load, go offline, reload (served from cache), record a visit with GPS, notes and a blurred photo, reconnect, and it synced (201). The stored photo had no EXIF.
  - Blurring is manual; there is no automatic face detection.
  - The demo project has one open field task (#320) to try this with `demo_field`.

## Uploading imagery (FR-022)

**Upload imagery** on a project page accepts a zipped Sentinel-2 Level-2A `.SAFE` folder (up to 4 GB, `MINEFOOT_MAX_UPLOAD_GB`).
- **Transfer:** 8 MB chunks, each with its SHA-256. The server keeps a chunk only if it starts at the stored byte count and its hash matches. Failures are retried with backoff, and choosing the same file again (even after a reload) resumes from the last confirmed byte.
- **Tested:**
  - API test: three interruptions (corrupted chunk, resent old chunk, connection cut mid-chunk).
  - Browser: a cut connection plus a page reload resumed at 55% and finished with the original file's SHA-256.
  - A 2 GB transfer has not been tried.
- **Validation:** on completion the whole-file SHA-256 is recorded and the product is checked. Level-1C, missing bands and non-zips are refused with the reason. The baseline, offsets and footprint come from `MTD_MSIL2A.xml`.
- **Use in jobs:** ready uploads covering the area and window are offered to the job alongside catalogue scenes. They are read in place through `/vsizip` and go through the same `fetch_scene` → `prepare_scene` path. A package test shows identical reflectance to the same scene fetched normally. A job still runs from uploads when every catalogue is down.

## Cost and quotas (FR-086, FR-087)

- **Estimate:** compute time and storage come from the medians of the organisation's last finished jobs (at least three), otherwise from defaults. On the Ghana jobs the calibrated estimate is 43 s for 4 tiles; the three real runs took 51, 40 and 43 s, all within 25%.
- **Actual:** when a job ends, its wall time, tiles processed, bytes kept on disk and the ratio to the estimate are recorded (`Job.actual`).
- **Money:** shown only when `MINEFOOT_CPU_COST_PER_HOUR` or `MINEFOOT_STORAGE_COST_PER_GB_MONTH` is set; otherwise the app says there is no money cost.
- **Quotas:** per organisation (Django admin, Organisation): km² per month, jobs per month and storage in GB; empty means no limit. The estimate shows quota status, and the start is refused with a reason when a quota would be exceeded.

## Restricted sites and validated domain (FR-005, FR-014)

- **Restricted sites:** GIS users or administrators mark a candidate's location restricted, with a reason; the change is audited. Restricted sites are:
  - hidden from customer viewers;
  - left out of every export, with the count in the manifest as `restricted_withheld`.
  - An administrator can grant a named user explicit access on the project page, and revoke it. Reviewers still see restricted sites, marked as such.
- **Validated domain:** for each area, and again in the estimate before a run, two measured facts:
  - the distance to the nearest locked test site of the active model;
  - whether any test scene within 1,000 km was taken in the same months as the job window.
  - Provisional rule: outside the domain if no test site is within 500 km, or no nearby test scene is from those months. The Ghana check area is inside (155 km). Jos is outside (1,391 km, and no June to August test scenes nearby). The Nigerian-domain warning on the model card stays until G3.

## Imagery sources: fallback and licence approval (FR-024, FR-025)

- **Fallback:** the estimate searches the approved catalogues in order and uses the first that answers. The job records which one did (`estimate.source`). A test simulates an outage of the first catalogue.
  - Pixel downloads do not switch catalogue mid-job; a tile whose download fails is marked failed with its reason.
- **Approval:** each project states its intended use (internal research, evaluation or commercial). Sources are approved per use in `SourceApproval`, seeded from `docs/licence_register.csv`; administrators edit them in the Django admin.
  - Planetary Computer is **not** approved for commercial use, because its terms say "solely for evaluation and testing".
  - The estimate refuses with the licence reason when no source is approved. An approval withdrawn after the estimate blocks the start.

## Provenance and progress (FR-021, FR-023, FR-053, FR-132)

- Each tile records SHA-256 checksums and the size of its fetched and prepared rasters. Each scene records when it was first retrieved.
- The coverage thresholds are settings: `MINEFOOT_COVERAGE_WARN_BELOW` (default 0.80) and `MINEFOOT_COVERAGE_BLOCK_BELOW` (default 0.40). Both are provisional.
- The job monitor shows the usable share of the whole area (FR-051), with unchecked tiles counted as not assessed.
- It also shows time so far, an estimate of time left from tiles done, and the imagery fetched. Jobs run on your own CPU and Sentinel-2 imagery is free, so there is no money cost to show.

## Threshold and minimum-size preview (FR-126)

On a finished job's page, choose a threshold and a minimum size, then **Preview**. The candidates are recomputed from the job's stored score maps with the job's own vectorise-and-merge code. The map shows candidates kept, added and removed; nothing is saved.
- At the job's own settings, the preview gives the job's result (tested; on the Ghana job it gave the same 18 candidates and 6,236.5 ha).
- **Save as a new job** (GIS) records the settings as a job derived from this one, with the same scenes and score maps. The original job, its candidates and its reviews are unchanged. The new job becomes the area's latest result for review and export.
- Jobs run before score maps were recorded per tile are backfilled by migration `projects.0005`.

## Missed sites and filters (FR-093, FR-094)

- **Add missed site** (GIS role, review workspace): outline disturbance the model did not find, with a reason.
  - It is stored apart from candidates, append-only like reviews, and linked to the latest finished job in that area.
  - It counts as a false negative, unless the tile at its centre had no usable imagery. That case is labelled "not a model miss" (FR-052).
  - Exports include a separate `missed_sites.geojson`, and the manifest gives the count.
  - Like reviewed candidates, missed sites reach training labels only through independent QC (FR-075).
- **More filters** in the review queue: score band, area of interest, size in ha, image dates, model and the latest review reason. They are applied on the server (`/api/projects/<id>/detections/?min_ha=…&reason=…`).

## Comparison: swipe, side by side, blink, dates (FR-127)

In review, **Compare** has three modes:
- **Swipe:** a divider you drag; arrow keys move it too.
- **Side by side:** two maps; panning or zooming either moves the other.
- **Blink:** flips between the two views on a timer, with pause, flip and speed controls.

What to compare with: any band preset, on the **same date** or on **another date**. Other dates are other finished jobs whose imagery covers the candidate (`/api/detections/<id>/dates/`). Each date shows its own quality mask, so cloud on either date is hatched, never shown as clean ground. Outlines, clicks, keyboard review and editing stay on the main map.

The review header has a **Job** picker, so an earlier job's candidates and reviews stay reachable after a newer job over the same area. By default you see the latest job for each area.

## Correction requests (FR-096, Commercial)

Anyone who can see a released candidate, customer viewers included, can **Request a correction** in review. The kinds are: not mining disturbance, boundary wrong, location sensitive, imagery out of date, or other.
- **Owner:** an administrator assigns a GIS reviewer or administrator on the **Corrections** page.
- **Outcome:** the owner accepts or declines with a written outcome, which the requester sees. Accepting can apply the fix: reject the candidate through the normal review rules, or restrict the location (FR-005).
- **Records:** every step is an immutable event and an audit record. The requester can withdraw an open request.
- **Tested:** the lifecycle, role rules, organisation isolation and the applied rejection.

## Signed exports (FR-109, Commercial)

Every export package's manifest lists each file's SHA-256 and size. It is signed with Ed25519 over canonical JSON (sorted keys, every field except the signature).
- **In the package:** `verify_export.py`, a standalone verifier that needs only `cryptography`, and `HOW_TO_VERIFY.txt`.
  - Recipients use the public key from the server (`/api/signing-key/`, shown in the export panel), never one from the package. A key inside the package would prove nothing.
  - On the project page, **Signed packages: verify one** checks a returned package against this server's key.
- **Production key:** set `MINEFOOT_SIGNING_KEY` to base64 of a random 32-byte seed. Generate one with `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`, and keep it secret and backed up.
  - Without it, a development key derived from `DJANGO_SECRET_KEY` is used, and every signature, the verifier and the UI say "development key".
- **Tested:** a valid package verifies. An altered file, a missing or added file, an edited manifest, a manifest re-hashed after altering a file, and a different key all fail. The standalone script gives the same answers, and so does the browser on a real package.

## Not built yet (next slices)

- **A raster tile server** (TiTiler) instead of whole-scene PNGs.
- **Ingesting accepted pool labels** into a minefoot dataset version for training. Exports exist; the adapter does not.
- **Automatic face and number-plate detection** in field photos. Blurring is manual.
