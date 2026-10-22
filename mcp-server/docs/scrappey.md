# Scrappey — full documentation

Scrappey (scrappey.com) is a pay-as-you-go web-data API. In this MCP it is the **fetch**
provider — the cheapest option in the research and the current default.

## Pricing (verified against scrappey.com, 2026-08)

- **Pay-as-you-go, no subscription, no monthly minimum.** Free trial, no card.
- **Balance never expires**; only successful requests are billed (failed scrapes free).
- Two modes on one balance:
  - **Direct scrape** — €0.10 / 1K (raw HTTP + proxy, no JS rendering).
  - **Full browser load** — €1.00 / 1K (real browser + proxy, renders JS, handles
    browser-verification flows).
- **Residential proxies included on both modes** (50M+ IPs, 195+ countries) — no
  separate proxy billing.
- Reference point: €1/1K full-browser vs ZenRows ~$6.90/1K, ScrapingBee ~$4.90/1K.

## Capability in this MCP

`fetch`, `browser_actions`, `screenshot`, and `session` (create/destroy). Scrappey has
no search/crawl/extract endpoints. With `DEFAULT_PROVIDER=scrappey` the MCP exposes
`web_fetch`, `web_browser_actions`, `web_screenshot`, `web_session`, plus
`web_search`, `web_crawl`, and `web_extract` as **gated emulations** (see below).

### Emulated `web_search` / `web_crawl` / `web_extract` (dual-run trigger)

When Scrappey is the only configured provider, calling an emulated tool without
the gate flag is **not executed**: the server returns an explanation of how
the emulation will function and its drawbacks (see `emulation.py` for the exact
payload). The caller explicitly re-invokes with the documented flag to run:

- `web_search` → one full-browser (`js_render=true`, €1/1K, ~20–40s) fetch of
  `https://www.bing.com/search?q=…`; the rendered HTML is parsed into the unified
  `SearchResult` shape (Bing `/ck/a` click-tracking URLs are decoded to their real
  destination). Browser mode is deliberate: live testing (2026-10) showed Bing serves
  stale cached result pages for *other* queries to Scrappey's rotating residential
  proxies in basic mode (4/4 probe requests off-query), while browser mode returns
  fresh relevant results. No scores/dates; promoted results can appear; markup or
  anti-bot changes can degrade parsing.
- `web_crawl` → breadth-first walk of same-host pages, one basic-mode fetch per
  page (up to `max_pages` billed requests), raw HTML content per page. No JS
  rendering, no partial results on page errors.
- `web_extract` → one Scrappey fetch plus one LLM call. Basic mode
  (`js_render=false`, €0.10/1K) strips the raw HTML to text locally;
  `js_render=true` uses full browser mode (€1/1K) and passes Scrappey's markdown
  conversion straight to the LLM (needed for JS-heavy pages). The page text is
  truncated to `EXTRACT_MAX_CHARS` (default 100k chars) before the call. The
  completion is returned as clean Markdown in the normal `web_extract` response
  shape (`{provider, url, format, data}`).

**Emulated `web_extract` needs an LLM key.** The summary call bills the harness LLM
account, not the Scrappey balance. The server reads `OPENROUTER_API_KEY` or
`HF_TOKEN` from its environment (a harness can inject them via the mcp.json `env`
block — same mechanism as look-n-see — or they can live in `.env`) and
`EXTRACT_MODEL` to pick the model (defaults: OpenRouter `deepseek/deepseek-chat`,
HuggingFace `deepseek-ai/DeepSeek-V4-Flash-0731`). Thinking is requested off
(`reasoning: {effort: none}` / `enable_thinking: false`) but that is best-effort —
some models or endpoints reject or ignore the param, which only raises cost and
latency, not output quality. The summary is LLM-generated: it can miss details or
hallucinate facts; it is not a lossless or structured extract.

The flag is deliberately absent from the published tool schemas; it is revealed only
by each tool's first-call explanation and accepted only on that follow-up call.

## Tools

| MCP tool | Scrappey capability | Notes |
|---|---|---|
| `web_fetch` | fetch | basic (`js_render=false`) or browser (`js_render=true`) mode |
| `web_browser_actions` | browser actions | run a sequence of `browserActions` and return page content + `execute_js` results |
| `web_screenshot` | screenshot | base64 PNG; `full_page` or `selector` capture |
| `web_session` | sessions | `op=create` / `op=destroy` lifecycle (`sessions.create` / `sessions.destroy`) |
| `web_search` | *(emulated)* | gated dual-run: one full-browser Bing SERP fetch, parsed results (see above) |
| `web_crawl` | *(emulated)* | gated dual-run: BFS over same-host links, one basic-mode fetch per page (see above) |
| `web_extract` | *(emulated)* | gated dual-run: one Scrappey fetch + one LLM summary (requires OPENROUTER_API_KEY/HF_TOKEN) |

## Two fetch modes (mapped from `web_fetch` params)

The unified `web_fetch` tool maps its params onto Scrappey's `requestType`:

| `web_fetch` param | Scrappey effect | Cost | What you get |
|---|---|---|---|
| `js_render: false` | `requestType: "request"` (direct HTTP) | €0.10/1K | Raw HTML (`solution.response`), no JS rendering. Good for simple searches / APIs / static pages. |
| `js_render: true` | `requestType: "browser"` (full browser) | €1.00/1K | Rendered page; JS-heavy sites (Reddit, AliExpress). Markdown via `solution.markdown`. |

### Markdown is browser-only

Scrappey converts pages to Markdown **only in browser mode**. Basic mode has no
browser, so the provider never sends `filter:["markdown"]` there — it returns the raw
HTML response and labels the result `format: "html"`, even if `format: "markdown"` was
requested. (Requesting markdown in basic mode otherwise errors with
`The filter key you provided markdown was not found`.)

### Other `web_fetch` params

- `premium_proxy` — accepted for interface parity; residential proxies are always
  included on both modes, so it is effectively always enabled.
- `proxy_country` — exposed on `web_fetch`, `web_browser_actions`, and
  `web_screenshot`; maps to Scrappey's `proxyCountry` (e.g. `"UnitedStates"`) to pick a
  region for the built-in rotating proxies. Use it to pin the geo, which otherwise
  varies per request.

## API reference (as implemented)

- Endpoint: `POST https://publisher.scrappey.com/api/v1?key=<API_KEY>`
- Auth: API key in the URL query parameter.
- Body (browser + markdown):
  ```json
  {
    "cmd": "request.get",
    "url": "https://example.com",
    "requestType": "browser",
    "markdown": true,
    "filter": ["markdown"]
  }
  ```
- Body (basic): `{"cmd": "request.get", "url": "...", "requestType": "request"}`
- Success check: `data == "success"`; otherwise `ProviderError` is raised.
- Response parsing: `solution.markdown` (browser+markdown) or
  `solution.response` / `solution.innerText` (otherwise); final URL from
  `solution.currentUrl`.

## What this MCP exposes vs. what Scrappey offers

The MCP exposes fetch, browser actions, screenshots, and session management
(`sessions.create` / `sessions.destroy` via `web_session`) as unified tools. Scrappey
also offers features that are **not** exposed through the MCP (use Scrappey's direct
API for these):

- **Session management caveat (verified 2026-10)** — the create/destroy lifecycle
  works, but a named session is destroyed once a `request.get` completes, so cookies
  and browser state do **not** persist across requests yet, despite the vendor docs
  (tested with custom names, UUIDs, browser and basic mode).
- **Persistent profiles** — state that survives beyond a session (not exposed).
- **AI extraction** — GPT-4 powered structured extraction (separate feature).
- **Custom proxies** — `proxy` (http/socks), `customHeaders`, `build` selection.

### Browser actions (`web_browser_actions`)

`web_browser_actions` runs a list of Scrappey `browserActions` on a URL (full browser
mode) and returns the rendered page content plus any `execute_js` results. Supported
action types: `click`, `type`, `goto`, `wait`, `wait_for_selector`,
`wait_for_function`, `wait_for_load_state`, `wait_for_cookie`, `execute_js`, `scroll`,
`hover`, `keyboard`, `dropdown`.

Common action params: `type` (required), `when`
(`beforelaunch`|`beforeload`|`afterload`, default `afterload`), `ignoreErrors`,
`timeout`. See the **`scrappey://playbook`** MCP resource (or
`docs/playbook.md`) for recipes — e.g. reading a full AliExpress item description
requires scrolling to trigger the lazy-load, clicking "View more", then extracting.

> **`javascript_return` requires `format="html"`.** The markdown filter
> (`filter:["markdown"]`) suppresses `execute_js` results. Use `format="html"` when
> you need the `javascript_return` array.

See the **`scrappey://playbook`** MCP resource (source:
`src/scrape_n_search/playbook.md`) for recipes — e.g. reading a full AliExpress item
description requires scrolling to trigger the lazy-load, clicking "View more", then
extracting.

## Behavior notes (from live testing)

- **Browser mode is slow** — roughly 20–40s per fetch (real browser render).
- **Residential proxy geo varies per request** — prices/currency can flip (saw
  Morocco/MAD on search, UK/GBP on an item page for the same site).
- **Reddit**: new-reddit search returns spam/irrelevant results; `old.reddit.com`
  works well in browser mode.
- **AliExpress**: search + item pages fetch fine in browser mode; full product
  descriptions are lazy-loaded (not in the initial HTML, and the legacy description API
  returns 404) — expand them with `browserActions` (click "View more") via the direct API.
- **DuckDuckGo html** challenges basic mode (bot CAPTCHA); Bing works in basic mode.

## Failover

If Scrappey errors, the router fails over to the next configured fetch provider (e.g.
Jina keyless). The `web_fetch` response names the actual serving provider, so a
Scrappey failure is visible as `provider: "jina"` rather than an error.
