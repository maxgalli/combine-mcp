# combine-mcp

MCP Server for searching multiple CERN documentation sites via a unified interface. Supports two site shapes: **MkDocs** (BM25 over the published `search_index.json`) and legacy **GitBook v2/CLI** (BM25 over the markdown files listed in `SUMMARY.md`, walked once per 24 h).

This server exposes two tools an LLM agent can use to discover and read
documentation from multiple CERN sites (ATLAS software, computing, databases,
batch, cloud, ML@CERN, SWAN, FTS3, and more) without crawling.

> **Read-only by design.** No write tools. Public sources need no
> credentials; auth-gated sources read a token from an environment variable
> at request time — tokens are never forwarded to the LLM
> (arcade.dev Secret Injection pattern).

## Architecture

```
LLM <--MCP/stdio|HTTP--> combine-mcp serve
                            |
                            +-- MkDocs sources (search_index.json -> BM25)
                            |   +-- atlas-sft, atlas-computing, atlas-databases
                            |   +-- batch, cloud, ml, swan
                            |
                            +-- GitBook sources (SUMMARY.md walk -> BM25)
                            |   +-- fts
                            |
                            +-- GitLab Files Raw API (live, on demand)
                                  https://gitlab.cern.ch/api/v4/...
```

Each MkDocs site publishes a search payload at `/search/search_index.json`.
The server downloads each index once per 24 h, builds in-memory BM25 rankers,
and serves search hits from them. GitBook sites (no search payload) are
indexed by walking `SUMMARY.md`, fetching each linked `.md` page from the
GitLab Files API in parallel (concurrency-limited), and building the BM25
ranker over title+body — also refreshed at most every 24 h. Markdown bodies
are pulled live from VCS on demand for both backends.

## Installation

```bash
pip install combine-mcp
```

Or with pixi:

```bash
pixi install
```

## Usage

### As an MCP server (stdio)

```bash
combine-mcp serve
```

Or with a custom config file:

```bash
combine-mcp serve --config /path/to/docs-sources.json
```

### As a remote MCP (Streamable HTTP)

```bash
combine-mcp serve --transport streamable-http --port 8000
```

This is the deployment shape used by MCP servers at `*.app.cern.ch/mcp`.

### Claude Desktop / opencode (stdio)

```json
{
  "mcpServers": {
    "docs": {
      "command": "combine-mcp",
      "args": ["serve"]
    }
  }
}
```

### opencode-style remote MCP

In `opencode.json`:

```json
"mcp": {
  "docs": {
    "type": "remote",
    "url": "https://combine-mcp.app.cern.ch/mcp",
    "oauth": false
  }
}
```

## Available tools

| Tool | Description |
|------|-------------|
| `search_docs` | BM25 search across a chosen documentation source (title + URL + snippet only); `source` param chooses which docs site to search |
| `fetch_doc` | Fetch one page's Markdown/outline from the source repository; supports `mode="markdown" \| "outline" \| "sections:<heading>"` |

### Supported doc sources

| Source ID | Documentation | Backend |
|-----------|---------------|---------|
| `atlas-sft` | [ATLAS software/Athena](https://atlas-software.docs.cern.ch) | MkDocs |
| `atlas-computing` | [ATLAS computing guide](https://atlas-computing.docs.cern.ch) | MkDocs (auth-gated) |
| `atlas-databases` | [ATLAS databases](https://atlas-databases.docs.cern.ch) | MkDocs (auth-gated) |
| `batch` | [HTCondor Batch](https://batchdocs.web.cern.ch) | MkDocs |
| `cloud` | [CERN Cloud](https://clouddocs.web.cern.ch) | MkDocs |
| `ml` | [ML@CERN](https://ml.docs.cern.ch) | MkDocs |
| `swan` | [SWAN (Jupyter)](https://swan.docs.cern.ch) | MkDocs |
| `fts` | [FTS3 (File Transfer Service)](https://fts3-docs.web.cern.ch/fts3-docs/) | GitBook (legacy CLI) |

## Available resources

| URI | Description |
|-----|-------------|
| `docs://sources` | Lists all registered documentation sources and metadata (URLs, VCS paths, freshness) |

## Configuration

### Source config file

Pass `--config /path/to/docs-sources.json` to point at a custom source
registry. The bundled `docs_sources.json` is used by default.

Each entry in the `sources` array describes one MkDocs site:

```json
{
  "sources": [

    // --- MkDocs public source (no credentials needed) ---
    {
      "id": "my-public-docs",
      "name": "My Public Docs",
      "search_index_url": "https://my-public-docs.example.com/search/search_index.json",
      "repo_url":         "https://gitlab.example.com/group/my-public-docs",
      "docs_site_url":    "https://my-public-docs.example.com"
    },

    // --- MkDocs auth-gated source (Bearer / OIDC token) ---
    {
      "id": "my-internal-docs",
      "name": "My Internal Docs",
      "search_index_url": "https://internal.example.com/search/search_index.json",
      "repo_url":         "https://gitlab.example.com/group/my-internal-docs",
      "docs_site_url":    "https://internal.example.com",
      "auth": {
        "env_var": "MY_INTERNAL_DOCS_TOKEN"
      }
    },

    // --- GitBook source (legacy CLI v2; walks SUMMARY.md) ---
    {
      "id": "my-gitbook-docs",
      "name": "My GitBook Docs",
      "source_type":    "gitbook",
      "repo_url":       "https://gitlab.example.com/group/my-gitbook-docs",
      "docs_site_url":  "https://my-gitbook-docs.example.com",
      "summary_path":   "SUMMARY.md",
      "default_branch": "master"
    }
  ]
}
```

For `source_type: "gitbook"`, `search_index_url` is omitted. The indexer
walks `summary_path` (default `SUMMARY.md`) in the repo to discover pages.
Set `default_branch` to whatever ref holds the rendered output (legacy
GitBook repos commonly use `master`; MkDocs sources default to `main`).

Then set the environment variable before starting the server:

```bash
export MY_INTERNAL_DOCS_TOKEN="<your-token>"
combine-mcp serve --config my-sources.json
```

The token is read once per request and attached as
`Authorization: Bearer <token>`. It is **never** surfaced in tool output
or passed to the LLM.

#### `auth` block fields

| Field | Default | Description |
|-------|---------|-------------|
| `env_var` | *(required)* | Name of the environment variable holding the credential. |
| `header` | `Authorization` | HTTP header to set. Change to `Private-Token` for GitLab PATs. |
| `prefix` | `Bearer ` | Prepended to the token value. Set to `""` for raw tokens (e.g. GitLab PATs). |

**GitLab PAT example:**

```json
"auth": {
  "env_var":  "MY_GITLAB_PAT",
  "header":   "Private-Token",
  "prefix":   ""
}
```

#### What happens when the env var is missing

If an LLM calls `search_docs` or `fetch_doc` on an auth-gated source and
the env var is unset, the tool returns a structured Recovery Guide — no
exception propagates to the agent:

```
Recovery steps:
• Set the environment variable $MY_INTERNAL_DOCS_TOKEN to a valid token
  before querying this source.
• Public sources (no auth required): my-public-docs
```

#### `docs://sources` resource

The MCP resource `docs://sources` lists every registered source and flags
auth-gated ones with the required env var name — useful for introspection:

```
Available documentation sources:

  - my-internal-docs       - My Internal Docs  [auth: $MY_INTERNAL_DOCS_TOKEN]
  - my-public-docs         - My Public Docs
```

## Design principles ([arcade.dev](https://www.arcade.dev/patterns) patterns)

The two tools are deliberately small and aligned with arcade.dev patterns:

- **Query Tool** — both tools are read-only.
- **Tool Description** — descriptions are written for LLM comprehension
  with explicit scope boundaries to avoid router confusion.
- **Multi-source Router** — `source` parameter routes requests to the
  correct documentation index; unknown values return a Recovery Guide
  listing valid sources.
- **Smart Defaults** — `limit=10`, `mode="markdown"`, `source="atlas-sft"`.
  Most calls pass zero optional args.
- **Constrained Input** — `source` is validated against the closed set
  of registered documentation sites.
- **Natural Identifier** — `fetch_doc` accepts a rendered URL, a relative
  path, or a direct `.md` path, and resolves each shape internally.
- **Token-Efficient Response** — search returns title / URL / snippet
  only (no body); body fetch is a separate call.
- **Progressive Detail / Operation Mode** — fetch supports
  `outline` (headings only) and `sections:<heading>` (one section)
  before the agent commits to the full body.
- **Resource Reference** — every search hit carries the public docs URL
  so it can be cited / re-fetched without paying the search index cost
  again.
- **Recovery Guide** — every error returns a structured string listing
  concrete next-tool calls (e.g. "call `search_docs` with `source="batch"`
  to find the correct URL first").
- **Idempotent / cacheable** — search indexes are cached for 24 h with
  staleness checks; Markdown fetches piggyback on HTTP caching.
- **Tool Versioning** — the package version (`__init__.py:__version__`)
  is the durable handle; pin it on the deployment side.

## Development

```bash
pixi run test          # Quick tests (mocked, no network)
pixi run test-cov      # With coverage
pixi run lint          # Pre-commit + pylint
pixi run check         # Lint + test
pixi run check-all     # Lint + all tests with coverage
```

Tests are fully offline — the BM25 index is fed a small fixture corpus,
and the GitLab raw fetcher is mocked. No CERN network access required.

## Relationship to sibling MCPs

| MCP | Scope |
|-----|-------|
| `combine-mcp` (this) | Multi-source: ATLAS software/computing/databases, Batch, Cloud, ML@CERN, SWAN |
| [`cernopendata-mcp`](../cernopendata-mcp) | CERN Open Data portal records, files, glossary |
| [`atlasopenmagic-mcp`](../atlasopenmagic-mcp) | ATLAS metadata catalogue (AMI), dataset / run-list lookups |

The three are designed to be loaded together in
[`open-data-assistant-config`](../open-data-assistant-config); each tool
description spells out its scope to keep the router's confusability low.

## License

[MIT](LICENSE)
