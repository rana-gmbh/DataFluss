# The template site datafluss.de (docs/website.md). Built from the repository's root:
#   docker build -f web/Dockerfile .
# It carries only the site and the few platform files it shares (the template check), not DataFluss itself.
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

ARG PIP_INDEX_URL=https://pypi.org/simple
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --require-hashes -r requirements.lock
COPY web/ web/
COPY core/__init__.py core/fieldtypes.py core/
COPY prints/__init__.py prints/presets.py prints/
COPY tables/__init__.py tables/template_check.py tables/
COPY tables/library/ tables/library/
ENV DATA_DIR=/data MEDIA_ROOT=/data/media PYTHONUNBUFFERED=1
RUN SECRET_KEY=build-only python web/manage.py collectstatic --noinput

# Only the data is writable, so a compromised process cannot change the code.
RUN useradd --system --uid 10001 web && mkdir -p /data/media && chown -R web /data
USER web

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=6s --start-period=10s --retries=18 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz/', timeout=5)"]
CMD ["sh", "web/start.sh"]
