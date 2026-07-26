FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY app ./app
RUN pip wheel --wheel-dir=/wheels .

FROM python:3.12-slim
ARG PUID=1000
ARG PGID=1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid "${PGID}" appgroup \
    && useradd --uid "${PUID}" --gid appgroup --create-home appuser
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY --chown=appuser:appgroup app ./app
COPY --chown=appuser:appgroup scripts ./scripts
COPY --chown=root:root docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh
USER appuser
EXPOSE 8787
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "app.main"]
