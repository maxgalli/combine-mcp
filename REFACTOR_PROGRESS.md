# Multi-Source Refactor Progress

## Completed ✅

1. **Created `docs_sources.json`** with all 7 documentation sources:
   - atlas-sft (ATLAS Software)
   - atlas-computing (ATLAS Computing)
   - atlas-databases (ATLAS Databases)
   - batch (HTCondor Batch)
   - cloud (CERN Cloud)
   - ml (ML@CERN)
   - swan (SWAN/Jupyter)

2. **Created `config.py` module** with:
   - `DocSource` dataclass
   - `load_sources()` function to load from JSON
   - `get_default_sources()` to use embedded config
   - `validate_source_id()` for validation
   - `format_sources_guide()` for help messages

3. **Updated `tools/_index.py`**:
   - Modified `DocsIndex.__init__()` to accept `search_index_url` directly
   - Maintains backward compatibility with `docs_base` parameter
   - Automatically extracts `docs_base` from search_index_url

4. **Updated `server.py`**:
   - Updated imports to include config module
   - Changed instructions to mention multi-source capability
   - Modified `_make_mcp()` to load all sources
   - Changed lifespan to create multiple `DocsIndex` instances
   - Updated MCP name from "combine-mcp" to "docs-mcp"

5. **Updated `README.md`** to reflect multi-source design

## TODO 🔧

### High Priority
1. **Update `tools/search.py`**:
   - Add `source` parameter to `search_docs` tool
   - Validate source ID using `validate_source_id()`
   - Route to correct `DocsIndex` from indices dict
   - Update tool description and docstring
   - Add recovery guide with valid sources

2. **Update `tools/fetch.py`**:
   - Add `source` parameter to `fetch_doc` tool
   - Route to correct project based on source
   - Use source.project_id instead of hardcoded ID
   - Use source.gitlab_api_url if needed

### Medium Priority
3. **Update `cli.py`**:
   - Add `--config` option for custom docs_sources.json
   - Pass config path to server initialization

4. **Update tests**:
   - Add test fixtures for multiple sources
   - Test source validation and routing
   - Test error handling for invalid sources

### Low Priority
5. **Update `nomenclature.py`** if it has ATLAS-specific content
6. **Update `pyproject.toml`** with new package name and description
7. **Update `resources.py`** to expose `docs://sources` resource

## Notes
- The refactor maintains arcade.dev design principles
- Backward compatibility maintained where possible
- Default source is "atlas-sft" for ease of use
- All search indexes cached independently for 24 hours
