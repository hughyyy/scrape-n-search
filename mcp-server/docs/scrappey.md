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

`fetch` only. Scrappey has no search/crawl/extract endpoints; with
`DEFAULT_PROVIDER=scrappey` the MCP hides `web_search`, `web_crawl`, and `web_extract`,
exposing only `web_fetch`.

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
- `proxy_country` — maps to Scrappey's `proxyCountry` (e.g. `"UnitedStates"`) to pick a
  region for the built-in rotating proxies.

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

`web_fetch` covers the core fetch capability. Scrappey also offers features that are
**not** exposed through the MCP tool (use Scrappey's direct API for these):

- **Browser actions** — `browserActions` array on `request.get` (click, type, goto,
  wait, `execute_js`, scroll, hover, keyboard, dropdown, `wait_for_selector`,
  `wait_for_function`, `wait_for_load_state`, `wait_for_cookie`). Useful for clicking
  "View more" on lazy-loaded descriptions (e.g. AliExpress) or extracting DOM text via
  `execute_js`.
- **Session management** — persistent browser sessions (`session`), `sessions.create`,
  cookies, localStorage, `pageUrl` warm-up.
- **Screenshots** — full-page or element screenshots.
- **AI extraction** — GPT-4 powered structured extraction (separate feature).
- **Custom proxies** — `proxy` (http/socks), `customHeaders`, `build` selection.

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
