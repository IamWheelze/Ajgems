# Deploying minefoot (production)

One server with Docker runs the whole stack: PostGIS, Redis, the Django API (gunicorn), a Celery worker, Celery beat
(the daily drift check, FR-105), and Caddy, which serves the built React app and gets HTTPS certificates itself.

**Ask the founder first:** a server costs money (CLAUDE.md rule).

## 1. Server
- A Linux server with Docker Engine and the compose plugin. Sizing: 4 CPU and 16 GB RAM is enough for CPU detection
  jobs of a few hundred tiles. Allow 100 GB of disk for scene caches and job outputs.
- A DNS name pointing at the server, with ports 80 and 443 open.

## 2. Secrets (`web/.env`)
```bash
cp web/.env.example web/.env
```
Fill in every empty value. The commands that generate each key are in the file. Never commit `web/.env`; git
ignores it already.
- `MINEFOOT_MFA_KEY` encrypts TOTP secrets. If you lose it, every user must enrol again.
- `MINEFOOT_SIGNING_KEY` signs export packages (FR-109). Back it up: verifiers need it to check old packages.
- The backend refuses to start without these two keys when `DJANGO_DEBUG=0` (the default in the image).
- CDSE keys are optional; when both are set, Copernicus Data Space is the first imagery source.

## 3. Model runs
Jobs score with the production model from the registry, so its run folder must be on the server. Copy `data/runs/`
(at least `g2_hn/` or the approved run) to the server. Point `MINEFOOT_RUNS_DIR` at it, or put it at `../data/runs`
relative to `web/`. It is mounted read-only at `/data/runs`.

## 4. Start
```bash
docker compose -f web/docker-compose.prod.yml --env-file web/.env up -d --build
```
`migrate` runs the database migrations (including the append-only audit triggers) and `collectstatic`, then exits.
The other services wait for it.

## 5. First users
```bash
docker compose -f web/docker-compose.prod.yml --env-file web/.env exec backend python manage.py add_user
```
Passwords are typed at a hidden prompt. Privileged users must enrol TOTP before they can do anything (FR-001).
To register the model runs:
```bash
docker compose -f web/docker-compose.prod.yml --env-file web/.env exec backend python manage.py register_model --run /data/runs/g2_hn --user <your-username>
```
Approval is a two-person release review in the app (Registry page). The same person cannot register or propose and
then approve.

## 6. Backups
- Database: `docker compose ... exec db pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup.sql`, daily, kept off the server.
- Volume `minefoot-data`: job outputs, uploads, field photos and exports.
- `web/.env`, stored in a password manager.

## 7. Checks after deploying
```bash
docker compose -f web/docker-compose.prod.yml --env-file web/.env exec backend python manage.py check --deploy
```
Expect only W021 (HSTS preload, deliberately off). Then sign in and run a small job on a non-Nigerian test area.
Jobs over the Nigerian labelling regions are refused until the test lock exists.

## What was verified (2026-11-13, in the cloud session)
- `docker compose config` validates the file.
- The frontend build steps (`npm ci`, `npm run build`) pass in `node:22-slim`.
- The backend's system packages resolve on Debian trixie (GDAL 3.10.3, GEOS 3.13.1, PROJ 9.6.0).
- `manage.py check --deploy` passes, apart from W021.
- The `daily_drift_check` task runs.

What was not verified: a full image build and a running stack. The session's network proxy blocks package
downloads inside `docker build`, and Docker Hub rate-limited image pulls. Do one trial run on the server with
`DOMAIN=localhost` before going public.
