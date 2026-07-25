# Orion — Personal Knowledge Operating System.
# CPU-only, small-footprint image for the 8GB Arch host. No torch / no GPU:
# fastembed brings ONNX (bge-small-en-v1.5) and sqlite-vec is a single loadable file.

# ---- stage 1: build the React SPA (interfaces/spa -> dist) --------------------
# Node is only needed at build time; none of it ships in the final image.
FROM node:22-slim AS spa
RUN npm install -g pnpm@10
WORKDIR /spa
# deps first for layer caching; onlyBuiltDependencies in package.json lets esbuild build
COPY interfaces/spa/package.json interfaces/spa/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY interfaces/spa/ ./
RUN pnpm build

# ---- stage 2: the Python runtime ---------------------------------------------
# Python 3.13 (not the host's 3.14) purely for reliable manylinux wheel availability —
# the app is version-agnostic (the Jinja cache_size=0 workaround is harmless here).
FROM python:3.13-slim-bookworm

# libgomp1: OpenMP runtime onnxruntime needs. curl: the container HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps first for layer caching; then the source.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The built SPA (dist is .dockerignored from the context, so it comes only from the node stage).
COPY --from=spa /spa/dist ./interfaces/spa/dist

ENV PYTHONUNBUFFERED=1

EXPOSE 8020
# start-period covers the one-time ~5min fastembed model download on the very first boot
# (persisted afterwards via the /tmp/fastembed_cache volume, so later starts are seconds).
HEALTHCHECK --interval=30s --timeout=5s --start-period=360s --retries=3 \
    CMD curl -fs http://localhost:8020/health || exit 1

# Bind 0.0.0.0 for LAN reach (host networking puts this straight on the host's :8020).
CMD ["python", "-m", "uvicorn", "orion.core.api.app:app", "--host", "0.0.0.0", "--port", "8020"]
