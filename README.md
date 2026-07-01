# combine-mcp

An MCP server exposing the CMS Combine corpus to any MCP-aware LLM client
(Claude Desktop, Claude Code, opencode, Cursor, …). One server, four
sources, two tools, no embeddings.

| | |
|---|---|
| Corpus | Combine docs, paper, source code, cms-talk forum |
| Retrieval | BM25 in-memory (via `rank_bm25`) |
| Transport | stdio (default) or streamable HTTP |
| Auth | none — all sources are public or local |
| Read-only | yes; no write tools |

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
   │     │     submodule walk,            │
   │     │     one file = one document    │
   │     │                                │
   │     └── combine-forum ───────────────┘
   │           scraped Discourse JSONs,
   │           one topic = one document
```

The corpus assets are vendored under `corpora/`:

```
corpora/
├── paper_clean.txt           ← cleaned text of arXiv:2404.06614v2
└── forum/                    ← Discourse scrape (combine-mcp scrape)
    ├── topic_*.json
    ├── topic_*.txt
    └── .manifest.json
```

The Combine source tree (`combine-code`) is **fetched on demand** from
GitHub at the pinned tag (`v10.6.0`); it's not vendored locally, so
there's no submodule to init.

## Installation

```bash
git clone <repo-url> combine-mcp
cd combine-mcp
uv venv .venv
uv pip install --python .venv -e .
```

## Populating the forum corpus

The `combine-docs`, `combine-paper`, and `combine-code` sources are
ready immediately after the clone. The `combine-forum` source is empty
until you run the scraper:

```bash
# 1. Grab a cms-talk session cookie from your browser:
#    devtools → Application → Cookies → cms-talk.web.cern.ch
#    Copy the values of BOTH `_forum_session` and `_t`.
export DISCOURSE_COOKIE='_forum_session=<v>; _t=<v>'

# 2. Smoke test with a small limit:
./.venv/bin/combine-mcp scrape --limit 3

# 3. Run the full scrape (~20-30 min the first time; incremental afterwards):
./.venv/bin/combine-mcp scrape
```

Subsequent runs are incremental — only topics whose `last_posted_at`
has changed are re-fetched. The manifest at `corpora/forum/.manifest.json`
tracks this state.

Alternatively, if you have a recent scrape elsewhere (e.g., from a
sibling `combine-bot` repo), you can `rsync` it in:

```bash
rsync -avh ~/path/to/scraped/forum/ corpora/forum/
```

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
available on the four Combine sources.

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

| `mode` | All sources | `combine-forum` only |
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
| `combine-forum` | cms-talk Statistics category | Per-topic BM25 over scraped Discourse JSONs | dir mtime + 24-h TTL |

The agent can introspect this list at runtime via the `docs://sources`
MCP resource.

## Resources

| URI | Description |
|---|---|
| `docs://sources` | Markdown listing every registered source with its name and URLs. Useful for "what's available?" introspection. |

## CLI

```
combine-mcp serve [--transport stdio|streamable-http] [--host HOST] [--port PORT] [--config PATH]
combine-mcp scrape [--output PATH] [--full] [--sleep SECONDS] [--limit N]
```

`scrape` runs the cms-talk scraper:

- Incremental by default — only refetches topics whose latest reply
  changed.
- `--full` rescrapes every topic (catches silent edits to old posts).
- `--limit N` is a debug knob — stops after N topics.

Cookies expire when your CERN SSO session does (typically days to
weeks). If you start getting 403s mid-run, re-grab and re-export
`DISCOURSE_COOKIE`; the manifest is incremental so the next run resumes
where the failed one stopped.

## Periodic scraping (cron)

For an unattended deployment, refresh the forum every two days:

```cron
0 4 */2 * * /usr/bin/env -i HOME=$HOME bash -c \
    'source $HOME/.combine-mcp.env && \
     cd /opt/combine-mcp && \
     ./.venv/bin/combine-mcp scrape \
     >> /var/log/combine-mcp/scrape.log 2>&1'
```

Where `~/.combine-mcp.env` is a `chmod 600` file containing your
`DISCOURSE_COOKIE` export. Cron runs with a stripped env, so the wrapper
sources the file explicitly.

For a personal Mac, skip cron — just run `combine-mcp scrape` by hand
when you want fresh data.

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
├── corpora/                                  ← local data (paper, forum)
│   ├── paper_clean.txt
│   └── forum/                                ← combine-mcp scrape output
├── src/combine_mcp/
│   ├── cli.py                                ← `combine-mcp serve|scrape`
│   ├── server.py                             ← FastMCP setup, lifespan, _build_index
│   ├── config.py                             ← DocSource + JSON loading
│   ├── scrape.py                             ← cms-talk Discourse scraper
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
