FROM node:22-bookworm-slim AS web
WORKDIR /build
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.12.3
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/reposcope ./src/reposcope
RUN uv sync --frozen --no-dev
COPY --from=web /build/dist ./apps/web/dist
ENV REPOSCOPE_HOME=/state REPOSCOPE_ALLOWED_ROOTS=/repositories
EXPOSE 8000
CMD ["/app/.venv/bin/reposcope", "serve", "--host", "0.0.0.0"]
