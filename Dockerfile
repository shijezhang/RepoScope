ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${NODE_IMAGE} AS web
WORKDIR /build
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS dependencies
ARG GIT_VERSION=1:2.47.3-0+deb13u1
RUN apt-get update \
    && apt-get install -y --no-install-recommends "git=${GIT_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '/repositories/*'
RUN pip install --no-cache-dir uv==0.12.3
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project --link-mode=copy
FROM dependencies AS app
COPY src/reposcope ./src/reposcope
RUN uv sync --frozen --no-dev --no-editable --no-cache
COPY --from=web /build/dist ./apps/web/dist
ENV REPOSCOPE_HOME=/state REPOSCOPE_ALLOWED_ROOTS=/repositories REPOSCOPE_WEB_DIR=/app/apps/web/dist
EXPOSE 8000
CMD ["/app/.venv/bin/reposcope", "serve", "--host", "0.0.0.0"]
