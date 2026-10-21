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
  2. `mcp-server/` — an MCP server exposing 7 unified tools backed by 8 providers with
     automatic selection + failover.

## Current state (all verified)

- **98 tests pass** (`mcp-server/tests`).
- Registered with Oh My Pi on this machine: `~/.omp/agent/mcp.json` →
  `scrape-n-search` (stdio, `python -m scrape_n_search`, cwd=`mcp-server`, 120s timeout).
- A real `SCRAPPEY_API_KEY` is set in `mcp-server/.env` (gitignored). `DEFAULT_PROVIDER=scrappey`
  is active → the MCP exposes **`web_fetch`, `web_browser_actions`, `web_screenshot`,
  `web_session`** (Scrappey capabilities; unsupported tools are auto-hidden). A
  `scrappey://playbook` resource guides agents on common actions.
- Git history: `dbf36d7` (research) → `4a4e649` (MCP server) → `3419fc4` (Scrappey) →
  `5204838` (tool hiding + default provider) → `db61c28` (.env loading) → `9b71243`
  (cwd-independent .env + OMP docs) → `a66e638` (handover + docs + Scrappey basic-mode
  fix) → `8fac903` (browser actions + screenshot + playbook) → `02ed4d6` (setup wizard
  + README rewrite) → `aa0aad5` (install.sh + harness registration) →
  `05cbaf3` (proxy_country) → `a19054d` (HTTP_TIMEOUT) → `50b0e03` (arg validation) →
  `fd72e55` (web_session) → `361961e` (CACHE_TTL) → next (LOG_LEVEL).

## Since the previous handover (Oct 2026 session)

- **`proxy_country`** exposed on `web_fetch`/`web_browser_actions`/`web_screenshot` —
  pins the Scrappey residential proxy region (fixes the geo-variance issue).
- **`HTTP_TIMEOUT`** env — provider client timeout (default 60s/connect 15s); raise
  to 120 for long browser sequences. Providers share one httpx client closed by the
  Router.
- **Argument validation** — `format` is a schema enum on fetch/actions; bounds checks
  (`max_results` 1–50, `max_pages` 1–100, `max_depth` 1–10, non-empty `actions`) return
  clear error JSON.
- **`web_session`** tool — Scrappey `sessions.create`/`sessions.destroy` lifecycle plus
  a `session` param on the three fetch-shaped tools.
- **`CACHE_TTL`** env — router TTL cache for identical `fetch` calls (bounded, 512
  entries); kills repeated billing in agent loops.
- **`LOG_LEVEL`** env — stdlib logging; INFO logs provider attribution + failover,
  DEBUG adds cache hits.

## How to run

```bash
cd /mnt/shared/Source/scrape-n-search
.venv/bin/python -m pytest mcp-server/tests -q   # tests
.venv/bin/scrape-n-search                         # stdio MCP server

# any machine, one line:
curl -fsSL https://raw.githubusercontent.com/hughyyy/scrape-n-search/main/install.sh | bash
```

Keys/config live in `mcp-server/.env` (auto-loaded; see `.env.example`).

## Key files

- `mcp-server/src/scrape_n_search/server.py` — MCP tools + tool hiding/default provider
- `mcp-server/src/scrape_n_search/router.py` — selection, priority, failover, TTL cache
- `mcp-server/src/scrape_n_search/providers/` — 8 provider adapters (`base.py` + one per provider)
- `mcp-server/src/scrape_n_search/__main__.py` — entrypoint + `.env` loading + logging
- `mcp-server/docs/scrappey.md` — full Scrappey documentation
- `mcp-server/src/scrape_n_search/playbook.md` — common-actions playbook (exposed as `scrappey://playbook` resource; packaged in the wheel)
- `mcp-server/README.md` — full MCP server documentation

## Known issues / notes (from live testing)

- **Scrappey basic mode returns raw HTML, not markdown** — markdown conversion is
  browser-only (`js_render=True`). The provider now handles this correctly (never sends
  `filter:["markdown"]` in basic mode).
- **`web_browser_actions` `javascript_return` requires `format="html"`** — the markdown
  filter (`filter:["markdown"]`) suppresses `execute_js` results. Documented in the
  playbook and tool description.
- **Scrappey browser mode is slow** (~20–40s/fetch) and uses residential proxies whose
  geo varies per request — pin with `proxy_country` (saw Morocco/MAD and UK/GBP for the
  same site).
- **Scrappey sessions do NOT persist across requests (verified 2026-10, live)** —
  `web_session` create/destroy works, but a named session is destroyed once a
  `request.get` completes (tested custom names, UUIDs, browser and basic mode), so
  cookies/localStorage state cannot be shared between calls yet. Documented in tool
  description + docs; check with the vendor before building on it.
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

- **Releases/packaging**: MIT license, CI + release workflows live, `v0.1.0` released
  (wheel/sdist attached). PyPI pending: claim `scrape-n-search-mcp` + trusted
  publisher (owner hughyyy / repo scrape-n-search / workflow release.yml) in PyPI
  admin, then push a tag to publish; verify with `uvx scrape-n-search-mcp`. One-line
  installer (`curl | bash`) is live and pipe-safe (tarball fetch into
  `~/.local/share/scrape-n-search/src`, `--ref` pinning, EOF-safe --ask).
- Cross-request session reuse (cookies/login state) is blocked by Scrappey's current
  request lifecycle — re-test when the vendor changes behavior.
- Scrappey AI extraction is **not** exposed via the MCP (only fetch, browser actions,
  screenshots, sessions). Could add `web_extract` for Scrappey via its direct API.
- `web_crawl`/`web_extract` are only served by Firecrawl (and ZenRows for extract); they
  return a clean "no configured provider" error when no key is set.
- The `main`-branch OMP JSON schema is stale (rejects even the working `hf-vision` entry);
  runtime validation is permissive — don't "fix" the config to satisfy that schema.

## Note

The CMP 170HX / Reddit / AliExpress research performed during a previous session was a
**test of tool calling** — it is not part of the deliverable and should be ignored.
