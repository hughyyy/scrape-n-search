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
MCPServer (server.py) ── 7 tools: web_search, web_fetch, web_browser_actions,
                                 web_screenshot, web_session, web_crawl, web_extract
   │
   ▼
Router (router.py) ── picks provider by priority + capability, fails over on error
   │
   ├── TinyFish   (search, fetch)
   ├── Tavily     (search, fetch)
   ├── Exa        (search, fetch)
   ├── ZenRows    (fetch, extract)
   ├── Scrappey   (fetch, browser_actions, screenshot, session)
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
  that provider cannot serve are **hidden** from the client — except `web_search` and
  `web_crawl`, which stay visible when an enabled Scrappey can emulate them (each call
  explains the emulation and requires an explicit follow-up flag before executing).

## Tools

Every tool returns a JSON string. All accept an optional `provider` parameter to force a
specific provider; otherwise the default/auto-selected provider is used.

### `web_search`

Search the web and return ranked results.

| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | required |
| `max_results` | int | 5 | 1–50 |
| `provider` | string? | null | force a provider |
| `freshness` | string? | null | `hour` \| `day` \| `week` \| `month` \| `year` (mapped per provider) |
| `country` | string? | null | geo-boost (Tavily, Firecrawl, Serper) |

Response: `{query, provider, results: [{title, url, snippet, published_date?, score?}]}`

> **No search provider configured?** When only Scrappey is available, the call is not
> executed: the server returns an explanation of the emulated path (one full-browser
> Bing SERP fetch, parsed into the same shape; browser mode is required — live testing
> showed Bing serves stale cached result pages to Scrappey's basic-mode proxies) with
> its drawbacks, and a documented follow-up argument that runs it anyway. Real search
> providers (Tavily, Exa, Serper, TinyFish, Jina) are always preferred when configured.

### `web_fetch`

Fetch a URL to clean content, server-side.

| Param | Type | Default | Notes |
|---|---|---|---|
| `url` | string | — | required |
| `format` | string | `markdown` | `markdown` \| `html` (enum) |
| `provider` | string? | null | force a provider |
| `js_render` | bool | false | render JS (Scrappey browser mode; ZenRows `js_render`) |
| `premium_proxy` | bool | false | residential proxy routing (Scrappey: always on; ZenRows `premium_proxy`) |
| `proxy_country` | string? | null | pin the Scrappey residential proxy region (e.g. `UnitedStates`); otherwise geo varies per request |
| `session` | string? | null | attach the request to a Scrappey session (see `web_session`; state does not persist across calls) |

Response: `{provider, url, title?, format, content}`

### `web_browser_actions`

Run a sequence of browser actions on a URL (Scrappey full-browser mode) and return the
page content plus any `execute_js` results. Served by Scrappey only.

| Param | Type | Default | Notes |
|---|---|---|---|
| `url` | string | — | required |
| `actions` | array of objects | — | required — Scrappey action objects (`click`, `type`, `scroll`, `execute_js`, `wait`, `wait_for_selector`, `goto`, `hover`, `keyboard`, `dropdown`, …) |
| `format` | string | `markdown` | `markdown` \| `html`. Use `html` to receive `execute_js` results in `javascript_return` (the markdown filter suppresses them). |
| `provider` | string? | null | |
| `proxy_country` | string? | null | pin the Scrappey residential proxy region (e.g. `UnitedStates`) |
| `session` | string? | null | attach to a Scrappey session (see `web_session`) |

Response: `{provider, url, format, content, javascript_return[]}`

See the **`scrappey://playbook`** resource (or `src/scrape_n_search/playbook.md`) for
recipes — e.g. reading a full AliExpress item description (scroll + click "View more" +
extract).

### `web_screenshot`

Capture a screenshot of a URL (Scrappey browser mode), returned as base64 PNG. Served
by Scrappey only.

| Param | Type | Default |
|---|---|---|
| `url` | string | — |
| `full_page` | bool | false |
| `selector` | string? | null |
| `provider` | string? | null |
| `proxy_country` | string? | null | pin the Scrappey residential proxy region (e.g. `UnitedStates`) |
| `session` | string? | null | attach to a Scrappey session (see `web_session`) |

Response: `{provider, url, format: "png", screenshot_base64}`

### `web_session`

Manage Scrappey browser-session lifecycle (Scrappey-only). Create a session, attach
requests to it via the `session` parameter of `web_fetch` / `web_browser_actions` /
`web_screenshot`, and destroy it when done.

| Param | Type | Default | Notes |
|---|---|---|---|
| `op` | string | — | `create` \| `destroy` (enum) |
| `session` | string | — | required — session name |
| `provider` | string? | null | |

Response: `{provider, op, session, solution}`

> **Known limitation (verified 2026-10):** the create/destroy lifecycle works, but
> Scrappey currently destroys a named session once a `request.get` completes, so
> cookies/browser state do **not** persist across calls yet despite the vendor docs.
> Treat `session` as a lifecycle-managed name; re-create sessions as needed.

### `web_crawl`

Recursively crawl a domain. Served by Firecrawl; with only Scrappey configured, a
gated emulation (per-page basic-mode fetches, BFS over same-host links) is available
via the call-time explanation.

| Param | Type | Default |
|---|---|---|
| `url` | string | — |
| `max_pages` | int | 10 | 1–100 |
| `max_depth` | int | 2 | 1–10 |
| `provider` | string? | null |

Response: `{provider, pages: [{url, title?, content?}]}`

### `web_extract`

Extract structured data from a page. Served by Firecrawl (prompt-based) and ZenRows
(`extract=auto`). With only Scrappey configured, a gated emulation is available via
the call-time explanation: one Scrappey fetch plus one LLM call (the harness
`OPENROUTER_API_KEY`/`HF_TOKEN`, thinking requested off) that reduces the page to
clean Markdown.

| Param | Type | Default |
|---|---|---|
| `url` | string | — |
| `prompt` | string? | null |
| `provider` | string? | null |
| `js_render` | bool | false | emulation only: full browser mode (€1/1K) for JS-heavy pages |

Response: `{provider, url, data}` (`data` is Markdown for the Scrappey emulation)

## Provider coverage

| Provider | search | fetch | browser_actions | screenshot | session | crawl | extract | Requires |
|---|---|---|---|---|---|---|---|---|
| TinyFish | ✅ | ✅ | | | | | | `TINYFISH_API_KEY` |
| Tavily | ✅ | ✅ | | | | | | `TAVILY_API_KEY` |
| Exa | ✅ | ✅ | | | | | | `EXA_API_KEY` |
| ZenRows | | ✅ | | | | | ✅ | `ZENROWS_API_KEY` |
| **Scrappey** | | ✅ | ✅ | ✅ | ✅ | | | `SCRAPPEY_API_KEY` |
| Firecrawl | ✅ | ✅ | | | | ✅ | ✅ | `FIRECRAWL_API_KEY` |
| Serper | ✅ | | | | | | | `SERPER_API_KEY` |
| Jina | ✅* | ✅ | | | | | | `JINA_API_KEY` for search; fetch is keyless |

`browser_actions` and `screenshot` are Scrappey-only capabilities (full-browser
features). `*` `s.jina.ai` (search) now requires an API key; `r.jina.ai` (fetch) still
works without one.

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
| `ENABLED_TOOLS` | Explicit comma-separated allow-list (`web_search,web_fetch,web_browser_actions,web_screenshot,web_crawl,web_extract`). Overrides the set derived from `DEFAULT_PROVIDER`. |
| `PROVIDER_PRIORITY` | Selection order for auto-picked providers (default: recommended stack order). |
| `HTTP_TIMEOUT` | HTTP timeout in seconds for provider API calls (default 60, connect 15). Scrappey browser requests take 20–40s — set 120 for long `web_browser_actions` sequences. |
| `CACHE_TTL` | TTL in seconds for cached `web_fetch` results (default 0 = off). Identical repeated fetches (same url/params/provider) are served from the router cache, so agents re-reading a page pay once. |
| `LOG_LEVEL` | Log verbosity: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL`. INFO logs which provider served each tool call and failover attempts; DEBUG adds cache hits. |
| `OPENROUTER_API_KEY` / `HF_TOKEN` | LLM key for the emulated `web_extract` (fetch + LLM summary). Read from the process env first — a harness can inject it via its mcp.json `env` block — or set here. |
| `EXTRACT_MODEL` | Model for the emulated extract call (defaults: OpenRouter `deepseek/deepseek-chat`, HuggingFace `deepseek-ai/DeepSeek-V4-Flash-0731`). |
| `EXTRACT_MAX_CHARS` | Page text cap (chars) sent to the LLM per emulated extract (default 100000). |
| `JINA_KEYLESS` | Set `0` to disable the keyless Jina fallback. |

Example — Scrappey as the only/default provider (fetch + browser actions + screenshots):

```bash
# mcp-server/.env
SCRAPPEY_API_KEY=your_key_here
DEFAULT_PROVIDER=scrappey   # web_search/web_crawl/web_extract are emulated via Scrappey (gated)
```

## Scrappey

Scrappey is the cheapest scraping option in the research (€0.10/1K direct, €1/1K full
browser; pay-as-you-go, no subscription, balance never expires, residential proxies
included, pay-per-success). It provides **fetch, browser actions, screenshots, and
session management (`web_session`)** in this MCP, plus **gated emulations of
`web_search`** (one full-browser Bing fetch), **`web_crawl`** (per-page basic-mode
BFS), and **`web_extract`** (fetch + LLM summary) for setups where Scrappey is the
only configured provider — the first call explains mechanics and drawbacks, and runs
only after an explicit follow-up flag.

Full documentation of its two modes, pricing, API, browser actions, and behavior:
**[`docs/scrappey.md`](docs/scrappey.md)**. A common-actions playbook for agents is
exposed as the **`scrappey://playbook`** MCP resource (source:
`src/scrape_n_search/playbook.md`).

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
