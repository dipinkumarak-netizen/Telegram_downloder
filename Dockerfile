ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.lock pyproject.toml VERSION ./
COPY app ./app
RUN pip wheel --wheel-dir=/wheels -r requirements.lock \
    && pip wheel --no-deps --wheel-dir=/wheels .

FROM ${PYTHON_IMAGE}
ARG PUID=1000
ARG PGID=1000
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Telegram Media Downloader" \
      org.opencontainers.image.description="Self-hosted Telegram media downloader" \
      org.opencontainers.image.source="https://github.com/dipinkumarak-netizen/Telegram_downloder" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid "${PGID}" appgroup \
    && useradd --uid "${PUID}" --gid appgroup --create-home appuser
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels telegram-media-downloader \
    && rm -rf /wheels
COPY --chown=appuser:appgroup app ./app
COPY --chown=appuser:appgroup scripts/telegram_login.py ./scripts/telegram_login.py
COPY --chown=root:root docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && install -d -m 0775 -o appuser -g appgroup \
        /data/database /data/session /data/config /data/logs /data/tmp /downloads
USER appuser
EXPOSE 8787
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "app.main"]
