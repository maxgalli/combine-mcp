#!/bin/sh
# Entrypoint for the combine-mcp container.
#
# Fetches the SSO-gated forum corpora (cms-talk + HyperNews) at RUNTIME so
# the access token is never baked into the image. The corpora live in a
# private CMS-restricted GitLab repo (cms-analysis/general/combine-mcp-corpus);
# the public arXiv paper corpus is already baked into the image.
#
# Required for the forum sources to work:
#   COMBINE_MCP_CORPUS_URL  full authenticated GitLab clone URL, e.g.
#     https://<deploy-token-user>:<deploy-token>@gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus.git
#   Provide it as an OpenShift secret (do not hardcode it).
#
# If it is unset or the clone fails, the server still starts — the
# combine-docs (live), combine-code (tarball) and combine-paper (baked)
# sources work; only combine-forum / combine-hypernews are unavailable.

set -eu

CORPORA="${COMBINE_MCP_CORPORA_DIR:-/app/corpora}"
# git needs a writable HOME; the arbitrary OpenShift UID may not have one.
export HOME="${HOME:-/tmp}"

if [ -n "${COMBINE_MCP_CORPUS_URL:-}" ]; then
  echo "combine-mcp: fetching forum corpora into ${CORPORA} ..."
  tmp="$(mktemp -d)"
  # Redirect output so the token in the URL never reaches the logs.
  if git clone --depth 1 "$COMBINE_MCP_CORPUS_URL" "$tmp" >/dev/null 2>&1; then
    for d in forum hypernews; do
      if [ -d "$tmp/$d" ]; then
        rm -rf "${CORPORA:?}/$d"
        cp -a "$tmp/$d" "$CORPORA/$d"
      fi
    done
    echo "combine-mcp: corpora present: $(ls "$CORPORA")"
  else
    echo "combine-mcp: WARNING — corpus clone failed; combine-forum and" \
         "combine-hypernews will be unavailable this run." >&2
  fi
  rm -rf "$tmp"
else
  echo "combine-mcp: COMBINE_MCP_CORPUS_URL not set — forum corpora will" \
       "not be loaded (combine-forum / combine-hypernews unavailable)." >&2
fi

exec combine-mcp serve --transport streamable-http --port 8000
