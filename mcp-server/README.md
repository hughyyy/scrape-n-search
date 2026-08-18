# scrape-n-search MCP server

Unified web-search and scraping tools for AI agents, backed by multiple providers
(TinyFish, Tavily, Exa, ZenRows, Scrappey, Firecrawl, Serper, Jina). The agent calls a handful
of tools and never needs to know which provider served the request.

Because every provider fetches on **its own servers**, the client's IP (e.g. a VPN
IP that target sites reject) is never seen by the target site.

## Tools

| Tool | What it does | Returns |
|---|---|---|
| `web_search` | Web search | `{provider, query, results: [{title, url, snippet, published_date?, score?}]}` |
| `web_fetch` | Fetch a URL to clean content (server-side) | `{provider, url, title?, format, content}` |
| `web_crawl` | Recursive domain crawl | `{provider, pages: [{url, title?, content?}]}` |
| `web_extract` | Structured JSON extraction from a page | `{provider, url, data}` |

Every tool accepts an optional `provider` parameter to force a specific provider;
otherwise the first configured provider that supports the tool is used, with failover
to the next on error. The response always names the provider that served it.

## Configuration

API keys and settings live in **`mcp-server/.env`** (auto-loaded at startup; gitignored).
Copy `.env.example` to `.env` and fill in your keys. Existing environment variables take
precedence over `.env`, so you can also set keys in your shell or in the MCP client's
`env` block.

| Var | Effect |
|---|---|
| `DEFAULT_PROVIDER` | Provider used when a call omits `provider`. When set, tools that provider cannot serve are **hidden** from the client. |
| `ENABLED_TOOLS` | Explicit comma-separated allow-list (`web_search,web_fetch,web_crawl,web_extract`). Overrides the set derived from `DEFAULT_PROVIDER`. |
| `PROVIDER_PRIORITY` | Selection order for auto-picked providers (default: recommended stack order). |

Example — Scrappey as the only/default provider (fetch only):

```bash
# mcp-server/.env
SCRAPPEY_API_KEY=your_key_here
DEFAULT_PROVIDER=scrappey   # hides web_search/web_crawl/web_extract
```

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

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e mcp-server
```

Set API keys (see `mcp-server/.env.example`):

```bash
export TINYFISH_API_KEY=...      # recommended
export TAVILY_API_KEY=...        # recommended
export EXA_API_KEY=...           # recommended
export ZENROWS_API_KEY=...       # scraping / anti-bot
export SCRAPPEY_API_KEY=...     # scraping / anti-bot (cheapest, pay-as-you-go)
export FIRECRAWL_API_KEY=...    # crawl + extract
# optional: SERPER_API_KEY, JINA_API_KEY
# optional: PROVIDER_PRIORITY=tinyfish,tavily,exa,zenrows,scrappey,firecrawl,serper,jina
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
      "env": {
        "TINYFISH_API_KEY": "...",
        "TAVILY_API_KEY": "..."
      }
    }
  }
}
```

## Oh My Pi (OMP) integration

Registered on this machine as a user-level MCP server in `~/.omp/agent/mcp.json`
(`scrape-n-search`, stdio). After editing that file (or `.env`), reload in the
OMP session:

```
/mcp reload
/mcp list        # confirm scrape-n-search is discovered
/mcp test scrape-n-search
```

The server auto-loads `mcp-server/.env` regardless of the working directory, so
keys added there apply without touching the OMP config.

## Tests

```bash
.venv/bin/python -m pytest mcp-server/tests -q
```

Unit tests cover per-provider normalization (mocked HTTP), router selection/failover,
and the MCP surface end-to-end over an in-memory transport.
