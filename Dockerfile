FROM python:3.12-slim

# git is needed at runtime to fetch the private forum corpus (entrypoint).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Explicit, writable corpus directory. The bundled docs_sources.json uses
# ../../corpora/... paths that only resolve correctly from the source tree;
# once pip-installed they point at a bogus site-packages dir. config.py
# rebases the corpus paths onto this dir when the env var is set. Both the
# entrypoint (below) and config.py (at runtime) derive the location from it.
ENV COMBINE_MCP_CORPORA_DIR=/app/corpora
RUN mkdir -p "$COMBINE_MCP_CORPORA_DIR"

# The Combine paper corpus is the public arXiv text — safe to bake in.
# The SSO-gated forum corpora are NOT baked; they are cloned at runtime.
COPY corpora/paper_clean.txt "$COMBINE_MCP_CORPORA_DIR/paper_clean.txt"

# OpenShift runs the container as an arbitrary UID in group 0, so make the
# corpus dir group-owned and group-writable (the entrypoint clones into it).
RUN chgrp -R 0 "$COMBINE_MCP_CORPORA_DIR" && chmod -R g=u "$COMBINE_MCP_CORPORA_DIR"

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
