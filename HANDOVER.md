# HANDOVER — scrape-n-search

Handover for the next agent session. Project: **scrape-n-search** — research into
VPN-safe web-search/scraping APIs, plus an MCP server that rolls the providers behind
unified agent tools.

## What this project is

- **Problem solved**: requests from a VPN IP get rejected by target sites. The fix is
  server-side APIs — the provider fetches from its own IP pools, so the client IP never
  touches the target site.
- **Two parts**:
  1. `research/` — verified comparison of search + scraping providers (pricing, free
     tiers, prepaid/credit models).
  2. `mcp-server/` — an MCP server exposing 4 unified tools backed by 8 providers with
     automatic selection + failover.

## Current state (all verified)

- **41 tests pass** (`mcp-server/tests`).
- Registered with Oh My Pi on this machine: `~/.omp/agent/mcp.json` →
  `scrape-n-search` (stdio, `python -m scrape_n_search`, cwd=`mcp-server`, 120s timeout).
- A real `SCRAPPEY_API_KEY` is set in `mcp-server/.env` (gitignored). `DEFAULT_PROVIDER=scrappey`
  is active → the MCP exposes **only `web_fetch`** (Scrappey is fetch-only; unsupported
  tools are auto-hidden).
- Git history: `dbf36d7` (research) → `4a4e649` (MCP server) → `3419fc4` (Scrappey) →
  `5204838` (tool hiding + default provider) → `db61c28` (.env loading) → `9b71243`
  (cwd-independent .env + OMP docs).

## How to run

```bash
cd /mnt/shared/Source/scrape-n-search
.venv/bin/python -m pytest mcp-server/tests -q   # tests
.venv/bin/scrape-n-search                         # stdio MCP server
```

Keys/config live in `mcp-server/.env` (auto-loaded; see `.env.example`).

## Key files

- `mcp-server/src/scrape_n_search/server.py` — MCP tools + tool hiding/default provider
- `mcp-server/src/scrape_n_search/router.py` — selection, priority, failover
- `mcp-server/src/scrape_n_search/providers/` — 8 provider adapters (`base.py` + one per provider)
- `mcp-server/src/scrape_n_search/__main__.py` — entrypoint + `.env` loading
- `mcp-server/docs/scrappey.md` — full Scrappey documentation
- `mcp-server/README.md` — full MCP server documentation

## Known issues / notes (from live testing)

- **Scrappey basic mode returns raw HTML, not markdown** — markdown conversion is
  browser-only (`js_render=True`). The provider now handles this correctly (never sends
  `filter:["markdown"]` in basic mode).
- **Scrappey browser mode is slow** (~20–40s/fetch) and uses residential proxies whose
  geo varies per request (saw Morocco/MAD and UK/GBP for the same site).
- **Jina search (`s.jina.ai`) now requires an API key** (401 without); keyless fetch
  (`r.jina.ai`) still works.
- **DuckDuckGo html endpoint challenges basic mode** (bot CAPTCHA); Bing works in basic mode.
- **Reddit**: new-reddit search returns spam/irrelevant results; `old.reddit.com` works
  well in browser mode.
- **AliExpress**: search + item pages fetch fine in browser mode, but full product
  descriptions are lazy-loaded (not in initial HTML; the legacy description API 404s).
  Extracting descriptions may need Scrappey `browserActions` (click "View more") via the
  direct API — not exposed through the MCP `web_fetch` tool.
- **Failover works**: when the preferred provider errors, the router silently fails over
  (e.g. Scrappey → Jina). The response names the actual serving provider.

## Open items / next steps

- Consider exposing Scrappey `browserActions` (click/scroll/execute_js) as an optional
  MCP capability — currently `web_fetch` is fetch-only.
- `web_crawl`/`web_extract` are only served by Firecrawl (and ZenRows for extract); they
  return a clean "no configured provider" error when no key is set.
- The `main`-branch OMP JSON schema is stale (rejects even the working `hf-vision` entry);
  runtime validation is permissive — don't "fix" the config to satisfy that schema.

## Note

The CMP 170HX / Reddit / AliExpress research performed during a previous session was a
**test of tool calling** — it is not part of the deliverable and should be ignored.
