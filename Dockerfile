# OmniCloud: SPA build (node) -> app (python-slim + git/node for in-container updates)
FROM node:22-slim AS web
# /build/web mirrors the local layout: vite's outDir is '../server/static',
# relative to the vite root - so the output lands at /build/server/static
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build           # -> /build/server/static

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /repo
# deps first (cached while only code changes). README.md is needed by the
# build backend (pyproject readme=) - it changes rarely, so it stays here.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev
COPY server/ ./server/
COPY --from=web /build/server/static ./server/static

# git + node: the panel's built-in updater (Settings -> Panel update) runs
# INSIDE the container against a bind-mounted git checkout of this repo
# (DEPLOY.md "self-updating container"). The update applies with git pull ->
# uv sync -> npm build and exits 78; --restart reloads the new code.
# Self-update needs write access to the code tree, which the bind-mounted
# checkout only grants to root - so this image runs as root. See DEPLOY.md
# for the tradeoff (a self-updating container is root-in-container by
# design; the panel holds the master key either way).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# node + npm from the web stage (npm is a node_modules tree + shim, so both
# the binary and the lib directory come along)
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

# the prebuilt SPA for first boot of a bind-mounted checkout (the entrypoint
# copies it in; later builds happen inside the container via the updater)
RUN cp -r server/static /image-static
COPY docker-entrypoint.sh /entrypoint.sh
ENV OMNICLOUD_DB=/data/omnicloud.db
ENV OMNICLOUD_CONTAINER=1
ENTRYPOINT ["/entrypoint.sh"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"
CMD ["uv", "run", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
