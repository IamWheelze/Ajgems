# minefoot backend image: Django API (gunicorn), Celery worker and Celery beat, with the minefoot package and
# CPU PyTorch for detection jobs. Build from the repository root:
#   docker build -f web/backend/Dockerfile -t minefoot-backend .
FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# GeoDjango needs GDAL, GEOS and PROJ; trixie ships GDAL 3.10 (Django asks for 3.9 or later for its file/network guard).
RUN apt-get update && apt-get install -y --no-install-recommends gdal-bin libgdal-dev libgeos-c1t64 libproj25 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && pip install ".[web,g2]"
COPY configs ./configs
COPY docs ./docs
COPY web/backend ./web/backend
WORKDIR /app/web/backend
RUN useradd --create-home --uid 1000 minefoot && mkdir -p /data staticfiles && chown minefoot /data staticfiles
USER minefoot
ENV DJANGO_DEBUG=0 MINEFOOT_DATA_ROOT=/data
EXPOSE 8000
CMD ["gunicorn", "minefoot_web.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
