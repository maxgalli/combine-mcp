# combine-mcp

An MCP server exposing the CMS Combine corpus to any MCP-aware LLM client
(Claude Desktop, Claude Code, opencode, Cursor, …). One server, five
sources, two tools, no embeddings.

> **Want the full assistant** (skill + execution)? See
> [`combine-assistant`](https://github.com/maxgalli/combine-assistant),
> which bundles this retrieval server with the
> [`combine-run-mcp`](https://github.com/maxgalli/combine-run-mcp)
> execution server and the routing skill. This repo is just the
> retrieval MCP.

| | |
|---|---|
| Corpus | Combine docs, paper, source code, cms-talk forum, HyperNews forum (archived) |
| Retrieval | BM25 in-memory (via `rank_bm25`) |
| Transport | stdio (default) or streamable HTTP |
| Auth | none in the server itself, but the **forum corpora are CMS-restricted** — see [Corpus & access](#corpus--access) |
| Read-only | yes; no write tools |

> **Access note.** The forum sources (`combine-forum`, `combine-hypernews`)
> are scraped from **CERN-authenticated** forums and are **not public**.
> They live in a private CMS-restricted repo and must not be committed here.
> The public deployment is restricted to the CERN network. See
> [Corpus & access](#corpus--access).

## Architecture

```
LLM client                       combine-mcp serve
   │      ┌──────── MCP/stdio ────────►│
   │ ◄────┘                            │
   │                                   ├─ search_docs     ┐
   │                                   ├─ fetch_doc       │ tools
   │                                   └─ docs://sources  ┘ resource
   │                                   │
   │                       ┌───────────┴───────────┐
   │                       │  Lazy BM25 per source │
   │                       └─────┬────────┬────────┘
   │                             │        │
   │     ┌── combine-docs ───────┘        │
   │     │     MkDocs published index +   │
   │     │     GitHub raw bodies          │
   │     │                                │
   │     ├── combine-paper ───────────────┤
   │     │     local file split into      │
   │     │     sections                   │
   │     │                                │
   │     ├── combine-code ────────────────┤
   │     │     GitHub tarball @ v10.6.0,   │
   │     │     one file = one document     │
   │     │                                 │
   │     ├── combine-forum ───────────────┤
   │     │     cms-talk Discourse JSONs,   │
   │     │     one topic = one document    │
   │     │                                 │
   │     └── combine-hypernews ───────────┘
   │           archived HyperNews JSONs,
   │           one topic = one document
```

## Corpus & access

The five sources are backed differently, and — importantly — **not all of
the corpus is public**:

| Source | Where its data comes from | Public? |
|---|---|---|
| `combine-docs` | fetched live from the published MkDocs `search_index.json` + GitHub raw bodies | yes |
| `combine-code` | GitHub tarball at pinned tag `v10.6.0`, fetched on demand | yes |
| `combine-paper` | `corpora/paper_clean.txt`, vendored here (arXiv:2404.06614v2) | yes |
| `combine-forum` | cms-talk Statistics category (Discourse) | **no — CERN-authenticated** |
| `combine-hypernews` | archived HyperNews forums (higgs-combination, phys-stat; pre-2022) | **no — CERN-authenticated** |

The forum corpora are scraped from CERN SSO–gated forums, so they are
**not vendored in this public repo**. They live in a private,
CMS-restricted GitLab repo,
[`cms-analysis/general/combine-mcp-corpus`](https://gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus)
(which also holds the scrapers), and are fetched at deploy time — see
[Deployment](#deployment). Locally, only `corpora/paper_clean.txt` is
present after a clone; the `corpora/forum/` and `corpora/hypernews/`
directories are gitignored and must be pulled from that private repo for
the forum sources to work.

`combine-hypernews` is the **lower-priority** forum source: it predates
2022 (cms-talk) and may reference older Combine versions — prefer
`combine-forum`, fall back to HyperNews.

## Installation

```bash
git clone <repo-url> combine-mcp
cd combine-mcp
uv venv .venv
uv pip install --python .venv -e .
```

## Getting the forum corpus for local dev

`combine-docs`, `combine-code`, and `combine-paper` work immediately
after a clone (fetched live / vendored). The two forum sources need the
CMS-restricted corpus, which is **not** in this repo. For local
development, clone the private corpus repo and copy the forum dirs into
`corpora/` (both are gitignored here):

```bash
git clone https://gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus.git /tmp/corpus
cp -a /tmp/corpus/forum /tmp/corpus/hypernews corpora/
```

In the deployed server this happens automatically — see
[Deployment](#deployment).

### Refreshing the forum data

Both scrapers live in the
[corpus repo](https://gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus),
alongside the data they produce — `scripts/scrape_cms_talk.py`
(`combine-forum`; needs `httpx` + a `DISCOURSE_COOKIE`) and
`scripts/scrape_hypernews.py` (`combine-hypernews`). Regenerated corpora
are committed **there** and picked up here at deploy — the forum corpus
is never scraped into, or committed to, this (public) repo.

## Usage

### As an MCP server (stdio)

```bash
combine-mcp serve
```

Or with a custom source config:

```bash
combine-mcp serve --config /path/to/my-sources.json
```

### As a remote MCP (Streamable HTTP)

```bash
combine-mcp serve --transport streamable-http --port 8000
```

### Claude Code (project-scoped, easiest)

The repo ships a project-level [`.mcp.json`](.mcp.json) at the root
that auto-registers `combine-mcp` when you open the project. As long
as you've installed into `.venv/` (see
[Installation](#installation)), no config edit is needed. Verify with:

```
/mcp
```

inside a Claude Code session started from the repo root. You should
see `combine` listed with its two tools.

### Claude Desktop / user-scoped Claude Code / other clients

For registration outside a project checkout (Claude Desktop, Claude
Code user-scoped, opencode, Cursor, …), add this to the client's MCP
config. For Claude Desktop the file is
`~/Library/Application Support/Claude/claude_desktop_config.json`; for
other clients consult their MCP docs — the schema is the same:

```json
{
  "mcpServers": {
    "combine": {
      "command": "/absolute/path/to/combine-mcp/.venv/bin/combine-mcp",
      "args": ["serve"]
    }
  }
}
```

Restart the client. The agent now has `search_docs` and `fetch_doc`
available on the five Combine sources.

### Inspector (preview without an LLM client)

```bash
npx @modelcontextprotocol/inspector ./.venv/bin/combine-mcp serve
```

Open the URL printed in the terminal, click **Connect**, and use the
Tools tab to drive `search_docs` / `fetch_doc` by hand.

## Available tools

| Tool | Returns |
|---|---|
| `search_docs(query, source="combine-docs", limit=10)` | Token-efficient ranked hits: `{title, url, path, section, score, snippet}`. No body. |
| `fetch_doc(url_or_path, source="combine-docs", mode="markdown")` | The body of one document, projected through `mode`. |

### Fetch modes

| `mode` | All sources | forum sources (`combine-forum`, `combine-hypernews`) |
|---|---|---|
| `markdown` (default) | full body / section / file | full thread transcript |
| `outline` | `#`/`##`/`###` headings (docs/paper) or top-level defs (code) | list of posts: `{post_number, username, char_count, is_accepted_answer}` |
| `sections:<heading>` | one named section (docs/paper) | — |
| `post:<N>` | — | one specific post's body, with a per-post URL |
| `post:accepted` | — | the accepted-answer post (404-style recovery if the thread isn't solved) |

## Available sources

| Source ID | Corpus | Backend | Refresh |
|---|---|---|---|
| `combine-docs` | [Combine official docs](https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/latest) | MkDocs `search_index.json` + GitHub raw bodies | 24-h TTL |
| `combine-paper` | Combine paper (arXiv:2404.06614v2) | Single local text file split into sections | mtime + 24-h TTL |
| `combine-code` | Combine source tree at tag `v10.6.0` | Per-file BM25; tarball fetched from `codeload.github.com` on first search | 24-h TTL |
| `combine-forum` | cms-talk Statistics category (2022→) | Per-topic BM25 over scraped Discourse JSONs | dir mtime + 24-h TTL |
| `combine-hypernews` | archived HyperNews forums (pre-2022) — **lower priority** | Per-topic BM25 over scraped JSONs | dir mtime + 24-h TTL |

The agent can introspect this list at runtime via the `docs://sources`
MCP resource.

## Resources

| URI | Description |
|---|---|
| `docs://sources` | Markdown listing every registered source with its name and URLs. Useful for "what's available?" introspection. |

## CLI

```
combine-mcp serve [--transport stdio|streamable-http] [--host HOST] [--port PORT] [--config PATH]
```

The forum scrapers are not part of this package — they live in the
[corpus repo](https://gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus)
(see [Refreshing the forum data](#refreshing-the-forum-data)).

## Configuration

Pass `--config /path/to/sources.json` to override the bundled source
registry. Each entry's schema:

```jsonc
{
  "sources": [
    // --- MkDocs source: BM25 over a published search payload ---
    {
      "id": "my-mkdocs-site",
      "name": "My MkDocs Site",
      "source_type": "mkdocs",                        // optional, default
      "search_index_url": "https://.../search/search_index.json",
      "repo_url":         "https://github.com/<owner>/<repo>",
      "docs_site_url":    "https://...",
      "vcs_provider":     "github",                   // optional, default
      "default_branch":   "main"                      // optional, default
    },

    // --- Local text file (paper-style): BM25 over detected sections ---
    {
      "id": "my-paper",
      "name": "My Paper",
      "source_type":   "local-paper",
      "repo_url":      "https://arxiv.org/abs/0000.00000",
      "docs_site_url": "https://arxiv.org/abs/0000.00000",
      "local_path":    "../../corpora/my-paper.txt"   // relative to JSON dir
    },

    // --- Local file tree (code-style): one file = one BM25 document ---
    {
      "id": "my-code",
      "name": "My Code",
      "source_type":   "local-files",
      "repo_url":      "https://github.com/<owner>/<repo>",
      "docs_site_url": "https://github.com/<owner>/<repo>",
      "local_root":    "../../corpora/my-code",
      "include_globs": ["**/*.py", "**/*.h"],
      "url_template":  "https://github.com/<owner>/<repo>/blob/main/{relpath}"
    },

    // --- Remote GitHub tarball (code-style, no local checkout) ---
    {
      "id": "my-github-code",
      "name": "My GitHub Code",
      "source_type":   "github-tarball",
      "repo_url":      "https://github.com/<owner>/<repo>",
      "docs_site_url": "https://github.com/<owner>/<repo>",
      "default_branch": "v1.0.0",                     // git ref (tag or branch)
      "include_globs": ["**/*.py", "**/*.h"],
      "url_template":  "https://github.com/<owner>/<repo>/blob/{ref}/{relpath}"
    },

    // --- Local Discourse scrape (forum-style): one topic = one document ---
    {
      "id": "my-forum",
      "name": "My Forum",
      "source_type":   "local-forum",
      "repo_url":      "https://my-discourse.example.com/c/foo",
      "docs_site_url": "https://my-discourse.example.com",
      "local_root":    "../../corpora/my-forum",
      "include_globs": ["topic_*.json"]               // optional, default
    }
  ]
}
```

Relative paths in `local_path` / `local_root` are resolved against the
JSON config file's directory. Absolute paths pass through unchanged.

## Deployment

Deployed on CERN PaaS (OpenShift) over streamable HTTP, built from this
repo's `Dockerfile`. Two env vars make the private forum corpus available
at runtime without baking it — or its access token — into the image:

- **`COMBINE_MCP_CORPORA_DIR`** (set in the `Dockerfile` to `/app/corpora`):
  an explicit corpus directory. `config.py` rebases the bundled
  `../../corpora/...` source paths onto it. Those relative paths only
  resolve from the source tree; once the package is `pip install`ed they
  point at a bogus site-packages dir, so this override is what makes the
  local sources load in the container at all.
- **`COMBINE_MCP_CORPUS_URL`** (a runtime **secret**): a full authenticated
  clone URL for the private corpus repo, e.g.
  `https://<deploy-token-user>:<token>@gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus.git`.
  `docker-entrypoint.sh` clones it into `COMBINE_MCP_CORPORA_DIR` at
  startup (the token never touches the image or the logs). If it's unset
  or the clone fails, the server still starts — only the forum sources are
  unavailable.

The public arXiv paper (`combine-paper`) is baked into the image; the
CERN-authenticated forum corpora are cloned at startup as above.

Provide the secret to the deployment and restrict the route to the CERN
network (it serves CERN-authenticated content):

```bash
oc create secret generic combine-mcp-corpus \
  --from-literal=COMBINE_MCP_CORPUS_URL='https://<user>:<token>@gitlab.cern.ch/cms-analysis/general/combine-mcp-corpus.git'
oc set env deploy/combine-mcp-git --from=secret/combine-mcp-corpus
```

## Releasing

The version is single-sourced in `src/combine_mcp/__init__.py`
(`__version__`); `pyproject.toml` reads it via hatch (`dynamic =
["version"]`), so there is only one place to bump. To cut a release:

1. Bump `__version__` in `src/combine_mcp/__init__.py`, commit.
2. Tag it (matching the version):
   `git tag -a vX.Y.Z -m "combine-mcp vX.Y.Z"`.
3. Push branch and tag: `git push origin main && git push origin vX.Y.Z`.

The PaaS app builds from the branch (it rebuilds on push), so the tag is
for version hygiene / reproducibility, not a deploy pin.

## Design principles ([arcade.dev](https://www.arcade.dev/patterns) patterns)

The two tools are deliberately small:

- **Query Tool** — both tools are read-only.
- **Multi-source Router** — `source` parameter routes requests to the
  correct index; unknown values return a Recovery Guide listing the
  registered sources.
- **Smart Defaults** — `limit=10`, `mode="markdown"`, `source="combine-docs"`.
- **Constrained Input** — `source` is validated against the closed
  set of registered sources.
- **Natural Identifier** — `fetch_doc` accepts URLs, relative paths,
  bare section ids (paper), bare relpaths (code), or bare topic ids
  (forum) and resolves each shape internally.
- **Token-Efficient Response** — `search_docs` returns title / URL /
  snippet only; bodies are a separate `fetch_doc` call.
- **Progressive Detail / Operation Mode** — `mode` controls projection
  granularity (full / outline / one section / one post / accepted-only).
- **Recovery Guide** — every error returns a structured string listing
  concrete next-tool calls.
- **Idempotent / cacheable** — indexes are lazy-loaded and cached for
  24 h plus a mtime check on local sources.

## Development

```bash
./.venv/bin/pytest -q                   # full test suite (no network)
./.venv/bin/pytest tests/test_<m>.py    # one module
```

Tests are fully offline. HTTP clients (both the MkDocs one and the
GitHub-tarball one) are mocked with synthetic fixtures. No CERN
network access required to run the suite.

## Project layout

```
combine-mcp/
├── corpora/                                  ← paper_clean.txt only (public, vendored);
│   └── paper_clean.txt                           forum/ & hypernews/ are gitignored and
│                                                 fetched from the private corpus repo
├── Dockerfile                                ← PaaS image (fetches corpus at startup)
├── docker-entrypoint.sh                      ← clones private forum corpus on boot
├── src/combine_mcp/
│   ├── cli.py                                ← `combine-mcp serve`
│   ├── server.py                             ← FastMCP setup, lifespan, _build_index
│   ├── config.py                             ← DocSource + JSON loading
│   ├── docs_sources.json                     ← the source registry
│   ├── resources.py                          ← docs://sources MCP resource
│   ├── nomenclature.py                       ← the instructions blob
│   └── tools/
│       ├── search.py                         ← `search_docs` handler
│       ├── fetch.py                          ← `fetch_doc` handler (5-way dispatch)
│       ├── _index.py                         ← DocsIndex + shared BM25 helpers
│       ├── _paper_index.py                   ← PaperIndex
│       ├── _code_index.py                    ← CodeIndex (local tree)
│       ├── _remote_code_index.py             ← RemoteCodeIndex (GitHub tarball)
│       └── _forum_index.py                   ← ForumIndex
└── tests/                                    ← offline test suite
```

## License

[MIT](LICENSE)
