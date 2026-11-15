FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

ARG SOURCE_COMMIT=unknown
ARG PIP_INDEX_URL=https://pypi.org/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --require-hashes -r requirements.lock
COPY . .
ENV APP_VERSION=$SOURCE_COMMIT MEDIA_ROOT=/data/media BACKUP_ROOT=/data/backups XDG_CACHE_HOME=/tmp REQUIRE_DATA_VOLUME=1
RUN SECRET_KEY=build-only DEBUG=0 python manage.py collectstatic --noinput

# Only the data is writable, so a compromised process cannot change the code.
RUN useradd --system --uid 10001 schule && mkdir -p /data/media /data/backups /app/.secrets && chown -R schule /data /app/.secrets
USER schule

EXPOSE 8000
# Coolify keeps the running version until this passes on the new container.
HEALTHCHECK --interval=5s --timeout=6s --start-period=10s --retries=18 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz/', timeout=5)"]
CMD ["sh", "deploy/start.sh"]
