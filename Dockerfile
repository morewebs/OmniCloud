# OmniCloud: SPA build (node) -> app (python-slim)
FROM node:22-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build           # outputs to /build/server/static via vite outDir

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
# deps first (cached while only code changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY server/ ./server/
COPY --from=web /build/server/static ./server/static

ENV OMNICLOUD_DB=/data/omnicloud.db
RUN useradd -m omni && mkdir -p /data && chown -R omni /data /app
USER omni
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"
CMD ["uv", "run", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
