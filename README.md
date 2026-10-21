# scrape-n-search

A lightweight [MCP](https://modelcontextprotocol.io) server that gives agents web search
and scraping behind a few simple tools — **web_search, web_fetch, web_browser_actions,
web_screenshot, web_crawl, web_extract** — backed by multiple providers and normalised
into one consistent result shape.

Every request is executed **server-side**: the provider fetches from its own IP pool, so
the client's IP (including a VPN IP that many sites block) never reaches the target
site. The only outbound request is an authenticated HTTPS call to the provider API.
Results come back LLM-friendly (markdown by default), which makes this a good fit for
agentic coding and research tasks.

## Providers

Nine capabilities, eight providers. [Scrappey](https://docs.scrappey.com) is the
recommended default for scraping — it is the cheapest full-browser option in the
research (€0.10/1K direct requests, €1/1K full browser), pay-as-you-go with no
subscription, a balance that never expires, residential proxies included, and you only
pay for successful requests.

| Provider | search | fetch | browser_actions | screenshot | crawl | extract | Requires |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| TinyFish | ✅ | ✅ | | | | | `TINYFISH_API_KEY` |
| Tavily | ✅ | ✅ | | | | | `TAVILY_API_KEY` |
| Exa | ✅ | ✅ | | | | | `EXA_API_KEY` |
| ZenRows | | ✅ | | | | ✅ | `ZENROWS_API_KEY` |
| **Scrappey** *(recommended)* | | ✅ | ✅ | ✅ | | | `SCRAPPEY_API_KEY` |
| Firecrawl | ✅ | ✅ | | | ✅ | ✅ | `FIRECRAWL_API_KEY` |
| Serper | ✅ | | | | | | `SERPER_API_KEY` |
| Jina | ✅* | ✅ | | | | | `JINA_API_KEY` (search; fetch is keyless) |

`browser_actions` and `screenshot` are Scrappey-only capabilities (full-browser
features). `*` `s.jina.ai` (search) now requires an API key; `r.jina.ai` (fetch) still
works without one.

The router picks the first *enabled* provider (API key present) that supports the
requested tool, in priority order, and fails over to the next one on error. Set
`DEFAULT_PROVIDER=scrappey` in `.env` to make Scrappey the default for every call.

See `research/` for the original provider comparison (pricing, free tiers,
prepaid/credit models) that informed these choices.

## Tools

Every tool returns a JSON string and accepts an optional `provider` parameter to force
a specific provider; otherwise the default or auto-selected provider is used.

| Tool | Description |
| --- | --- |
| `web_search` | `query` (required), `max_results` (5), `freshness`, `country` → `{query, provider, results:[{title, url, snippet, published_date?, score?}]}` |
| `web_fetch` | `url` (required), `format` (`markdown` \| `html`), `js_render`, `premium_proxy`, `proxy_country` → `{provider, url, title?, format, content}` |
| `web_browser_actions` | `url` + `actions[]` (required), `format`, `proxy_country` → `{provider, url, format, content, javascript_return[]}` |
| `web_screenshot` | `url` (required), `full_page`, `selector`, `proxy_country` → `{provider, url, format: "png", screenshot_base64}` |
| `web_crawl` | `url` (required), `max_pages` (10), `max_depth` (2) → `{provider, pages:[{url, title?, content?}]}` |
| `web_extract` | `url` (required), `prompt` → `{provider, url, data}` |

Every response names the provider that actually served the request. When
`web_browser_actions` runs `execute_js`, use `format="html"` — the markdown filter
suppresses `javascript_return` results.

## Requirements

- Python 3.10+
- An API key for at least one provider (see the table above)

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e mcp-server

# Configure — or run the interactive setup wizard
./setup.sh   # pick providers, paste+validate each API key live, then set the
             # default provider and fallback priority; writes mcp-server/.env
cp mcp-server/.env.example mcp-server/.env
# edit mcp-server/.env and set the API keys you want to use

# Run (stdio MCP server)
.venv/bin/scrape-n-search
```

## Install & register with your agents (optional)

`./install.sh` installs the server into a dedicated user venv
(`~/.local/share/scrape-n-search/venv`) so the interpreter path embedded in agent
configs stays stable, then registers it with the MCP-aware harnesses it detects
(Oh My Pi, Claude Desktop, Claude Code, Cursor, VS Code). Registration merges a
`scrape-n-search` entry into each harness's own config file — other servers are
preserved, a backup is written before any change, and writes are atomic.

```bash
./install.sh            # install + prompt to register with detected harnesses
./install.sh list       # show detection + registration state
./install.sh register   # re-register (e.g. after adding a harness)
./install.sh uninstall  # unregister, then remove the venv
```

Register against specific harnesses with `--harness` (e.g. `./install.sh register
--harness omp --harness cursor`). Codex is detected but not yet registered (its
TOML schema is unsupported). Details: `scripts/install_mcp.py`.

## MCP client config

Add the server to your MCP client's configuration (adjust paths to your checkout):

```json
{
  "mcpServers": {
    "scrape-n-search": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "scrape_n_search"],
      "cwd": "/absolute/path/to/mcp-server"
    }
  }
}
```

## Configuration

Configuration lives in `mcp-server/.env` (see `mcp-server/.env.example`). The server
loads `.env` from its own project directory regardless of how it is launched, and
existing environment variables take precedence.

| Variable | Effect |
| --- | --- |
| `TINYFISH_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`, `ZENROWS_API_KEY`, `SCRAPPEY_API_KEY`, `FIRECRAWL_API_KEY`, `SERPER_API_KEY` | Enable the corresponding providers. |
| `JINA_API_KEY` | Enables Jina search (fetch is keyless). |
| `JINA_KEYLESS` | Set `0` to disable the keyless Jina fetch fallback (on by default). |
| `DEFAULT_PROVIDER` | Provider used when a call omits `provider`. When set, tools that provider cannot serve are hidden from the client. |
| `ENABLED_TOOLS` | Explicit tool allow-list, e.g. `web_fetch,web_browser_actions,web_screenshot`. Overrides the set derived from `DEFAULT_PROVIDER`. |
| `PROVIDER_PRIORITY` | Selection order for auto-picked providers (default: recommended stack order). |

## How it works

The server is a small router over provider adapters (`mcp-server/src/scrape_n_search/`).
Each tool call is translated into the active provider's API request and the response is
normalised into a shared model (`SearchResult`, `FetchedPage`, `CrawlPage`), so results
look identical whichever provider served them. If the active provider errors, the
router fails over to the next available one. The server itself speaks MCP over stdio —
no state, no HTTP endpoint, one process.

## Development

```bash
.venv/bin/python -m pytest mcp-server/tests -q
```

Unit tests cover per-provider response normalisation, router selection and failover,
and the MCP surface end-to-end. To add a provider, subclass `BaseProvider`
(`mcp-server/src/scrape_n_search/providers/base.py`), register it in `build_providers`,
and add a normalisation test.
