# scrape-n-search MCP server

Unified web-search and scraping tools for AI agents, backed by multiple providers
(TinyFish, Tavily, Exa, ZenRows, Scrappey, Firecrawl, Serper, Jina). The agent calls a
handful of tools and never needs to know which provider served the request.

Because every provider fetches on **its own servers**, the client's IP (e.g. a VPN IP
that target sites reject) is never seen by the target site.

## Architecture

```
MCP client (agent)
   │  MCP stdio protocol
   ▼
MCPServer (server.py) ── 4 tools: web_search, web_fetch, web_crawl, web_extract
   │
   ▼
Router (router.py) ── picks provider by priority + capability, fails over on error
   │
   ├── TinyFish   (search, fetch)
   ├── Tavily     (search, fetch)
   ├── Exa        (search, fetch)
   ├── ZenRows    (fetch, extract)
   ├── Scrappey   (fetch)
   ├── Firecrawl  (search, fetch, crawl, extract)
   ├── Serper     (search)
   └── Jina       (search*, fetch)
```

- **Normalization**: every adapter maps its provider-specific response into the shared
  models (`SearchResult`, `FetchedPage`, `CrawlPage`), so results look the same
  regardless of provider.
- **Selection**: the router uses the first *enabled* provider (API key present) that
  supports the requested tool, in priority order.
- **Failover**: if the chosen provider errors, the router tries the next candidate and
  aggregates errors if all fail. The response always names the provider that served it.
- **Default provider**: `DEFAULT_PROVIDER` forces a preferred provider for every call
  (the agent can still override per-call with the `provider` parameter). When set, tools
  that provider cannot serve are **hidden** from the client.

## Tools

Every tool returns a JSON string. All accept an optional `provider` parameter to force a
specific provider; otherwise the default/auto-selected provider is used.

### `web_search`

Search the web and return ranked results.

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | required |
| `max_results` | int | 5 | |
| `provider` | string? | null | force a provider |
| `freshness` | string? | null | `hour` \| `day` \| `week` \| `month` \| `year` (mapped per provider) |
| `country` | string? | null | geo-boost (Tavily, Firecrawl, Serper) |

Response: `{query, provider, results: [{title, url, snippet, published_date?, score?}]}`

### `web_fetch`

Fetch a URL to clean content, server-side.

| Param | Type | Default | Notes |
|---|---|---|---|
| `url` | string | — | required |
| `format` | string | `markdown` | `markdown` \| `html` |
| `provider` | string? | null | force a provider |
| `js_render` | bool | false | render JS (Scrappey browser mode; ZenRows `js_render`) |
| `premium_proxy` | bool | false | residential proxy routing (Scrappey: always on; ZenRows `premium_proxy`) |

Response: `{provider, url, title?, format, content}`

### `web_crawl`

Recursively crawl a domain. Served by Firecrawl only.

| Param | Type | Default |
|---|---|---|
| `url` | string | — |
| `max_pages` | int | 10 |
| `max_depth` | int | 2 |
| `provider` | string? | null |

Response: `{provider, pages: [{url, title?, content?}]}`

### `web_extract`

Extract structured JSON from a page. Served by Firecrawl (prompt-based) and ZenRows
(`extract=auto`).

| Param | Type | Default |
|---|---|---|
| `url` | string | — |
| `prompt` | string? | null |
| `provider` | string? | null |

Response: `{provider, url, data}`

## Provider coverage

| Provider | search | fetch | crawl | extract | Requires |
|---|---|---|---|---|---|
| TinyFish | ✅ | ✅ | | | `TINYFISH_API_KEY` |
| Tavily | ✅ | ✅ | | | `TAVILY_API_KEY` |
| Exa | ✅ | ✅ | | | `EXA_API_KEY` |
| ZenRows | | ✅ | | ✅ | `ZENROWS_API_KEY` |
| Scrappey | | ✅ | | | `SCRAPPEY_API_KEY` |
| Firecrawl | ✅ | ✅ | ✅ | ✅ | `FIRECRAWL_API_KEY` |
| Serper | ✅ | | | | `SERPER_API_KEY` |
| Jina | ✅* | ✅ | | | `JINA_API_KEY` for search; fetch is keyless |

\* `s.jina.ai` (search) now requires an API key; `r.jina.ai` (fetch) still works without one.

## Configuration

API keys and settings live in **`mcp-server/.env`** (auto-loaded at startup; gitignored).
Copy `.env.example` to `.env` and fill in your keys. The server loads `.env` from the
working directory *and* falls back to the project's own `mcp-server/.env`, so it works
regardless of how it is launched. Existing environment variables take precedence over
`.env`.

| Var | Effect |
|---|---|
| `SCRAPPEY_API_KEY` | Enables the Scrappey provider (fetch). |
| `TINYFISH_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`, `ZENROWS_API_KEY`, `FIRECRAWL_API_KEY`, `SERPER_API_KEY`, `JINA_API_KEY` | Enable the corresponding providers. |
| `DEFAULT_PROVIDER` | Provider used when a call omits `provider`. When set, tools that provider cannot serve are **hidden** from the client. |
| `ENABLED_TOOLS` | Explicit comma-separated allow-list (`web_search,web_fetch,web_crawl,web_extract`). Overrides the set derived from `DEFAULT_PROVIDER`. |
| `PROVIDER_PRIORITY` | Selection order for auto-picked providers (default: recommended stack order). |
| `JINA_KEYLESS` | Set `0` to disable the keyless Jina fallback. |

Example — Scrappey as the only/default provider (fetch only):

```bash
# mcp-server/.env
SCRAPPEY_API_KEY=your_key_here
DEFAULT_PROVIDER=scrappey   # hides web_search/web_crawl/web_extract
```

## Scrappey

Scrappey is the cheapest scraping option in the research (€0.10/1K direct, €1/1K full
browser; pay-as-you-go, no subscription, balance never expires, residential proxies
included, pay-per-success). It supports **fetch only** in this MCP.

Full documentation of its two modes, pricing, API, and behavior:
**[`docs/scrappey.md`](docs/scrappey.md)**.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e mcp-server
```

## Run

```bash
.venv/bin/scrape-n-search            # stdio MCP server
# or: .venv/bin/python -m scrape_n_search
```

## MCP client config

Claude Desktop / Claude Code `claude_desktop_config.json` or `.mcp.json`:

```json
{
  "mcpServers": {
    "scrape-n-search": {
      "command": "/abs/path/to/scrape-n-search/.venv/bin/python",
      "args": ["-m", "scrape_n_search"],
      "cwd": "/abs/path/to/scrape-n-search/mcp-server",
      "env": {
        "SCRAPPEY_API_KEY": "..."
      }
    }
  }
}
```

## Oh My Pi (OMP) integration

Registered on this machine as a user-level MCP server in `~/.omp/agent/mcp.json`
(`scrape-n-search`, stdio). After editing that file (or `.env`), reload in the OMP
session:

```
/mcp reload
/mcp list        # confirm scrape-n-search is discovered
/mcp test scrape-n-search
```

The server auto-loads `mcp-server/.env` regardless of the working directory, so keys
added there apply without touching the OMP config.

## Testing

```bash
.venv/bin/python -m pytest mcp-server/tests -q
```

Unit tests cover per-provider normalization (mocked HTTP), router selection/failover,
and the MCP surface end-to-end over an in-memory transport.

## Development — adding a provider

1. Create `src/scrape_n_search/providers/<name>.py` with a subclass of `BaseProvider`
   implementing the tools it supports (see `base.py` and an existing adapter).
   Normalize responses into the shared models.
2. Register it in `src/scrape_n_search/providers/__init__.py` (`build_providers` +
   `DEFAULT_PRIORITY`).
3. Add the env var to `.env.example` and the coverage table above.
4. Add a normalization test to `tests/test_providers.py` (mocked HTTP via
   `httpx.MockTransport`).
