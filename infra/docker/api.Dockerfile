# TracePilot API image. Installs all editable packages in dependency order.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System deps: git (ingestion + git_diff tool), ripgrep (repo_search tool),
# build-essential for any native wheels (tree-sitter).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ripgrep build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package manifests + sources. Layer ordering favors dependency stability.
COPY packages ./packages
COPY apps/api ./apps/api
COPY scripts ./scripts

RUN pip install --upgrade pip && bash scripts/install_packages.sh

# Pre-download the default fastembed model so first request is fast (best-effort).
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" || true

RUN mkdir -p /data/workspaces
EXPOSE 8000

CMD ["uvicorn", "tracepilot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
