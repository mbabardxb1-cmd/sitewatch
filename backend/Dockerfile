# syntax=docker/dockerfile:1.7
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Optional: a build behind a TLS-intercepting proxy can pass its CA certificate as the
# "extra_ca" build secret. It is used only during the build and never stored in the image.
# DejaVu fonts give PDF reports full Unicode coverage. Builds without access to the Debian
# mirror can pass --build-arg INSTALL_FONTS=false (reports then use Helvetica, which covers
# Western European text; other characters are replaced).
ARG INSTALL_FONTS=true
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ "$INSTALL_FONTS" != "true" ]; then echo "Skipping font installation"; exit 0; fi \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && APT_OPTS="" && if [ -s /run/secrets/extra_ca ]; then APT_OPTS="-o Acquire::https::CAInfo=/run/secrets/extra_ca"; fi \
    && apt-get $APT_OPTS update \
    && apt-get $APT_OPTS install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data

COPY pyproject.toml ./
COPY app ./app
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi \
    && pip install --no-cache-dir ".[sentry]"
COPY alembic.ini ./
COPY migrations ./migrations

USER app
ENV STORAGE_DIR=/data
EXPOSE 8000
# Migrations are applied by the dedicated `migrate` service in docker-compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
