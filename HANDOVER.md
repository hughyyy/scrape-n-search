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

- **383 tests pass — 100% line AND 100% branch coverage** (`coverage run --branch`,
  1137 statements / 390 branches; `coverage` is now a dev dependency so the claim
  is reproducible from the committed `pyproject.toml`). Coverage was already
  saturated, so the audits used curated mutation sweeps
  (comparison/boolop/not/const/else-drop mutants across every module: **276
  mutants, 0 survivors**) and direct behavioural probing. Round four had closed
  the remaining decision arms (parser end-tag short-circuits, `_cur is None`
  guards, Exa `search_type=""`, Scrappey-disabled default provider,
  `ENABLED_TOOLS` block skipping, `_hide_force_flags` non-dict schemas) plus
  seeded property fuzzing and a concurrent-fetch test. Live-verified earlier:
  force extract through the real Scrappey + OpenRouter keys.
- Registered with Oh My Pi on this machine: `~/.omp/agent/mcp.json` →
  `scrape-n-search` (stdio, `python -m scrape_n_search`, cwd=`mcp-server`, 120s timeout).
- A real `SCRAPPEY_API_KEY` is set in `mcp-server/.env` (gitignored). `DEFAULT_PROVIDER=scrappey`
  is active → the MCP exposes **`web_fetch`, `web_browser_actions`, `web_screenshot`,
  `web_session`** (Scrappey capabilities) plus **`web_search`, `web_crawl`, `web_extract`
  as gated emulations** (see below). A
  `scrappey://playbook` resource guides agents on common actions.
- Harness registration (`~/.omp/agent/mcp.json`) now injects `HF_TOKEN` +
  `OPENROUTER_API_KEY` into the scrape-n-search server env (same `omp token`
  mechanism as look-n-see) — the emulated `web_extract` LLM leg bills those
  accounts, not the Scrappey balance. The MCP stdio SDK allow-lists inherited
  env (`HOME,LOGNAME,PATH,SHELL,TERM,USER`); any key must be declared in the
  registration `env` block or `.env`.
- Git history: `dbf36d7` (research) → `4a4e649` (MCP server) → `3419fc4` (Scrappey) →
  `5204838` (tool hiding + default provider) → `db61c28` (.env loading) → `9b71243`
  (cwd-independent .env + OMP docs) → `a66e638` (handover + docs + Scrappey basic-mode
  fix) → `8fac903` (browser actions + screenshot + playbook) → `02ed4d6` (setup wizard
  + README rewrite) → `aa0aad5` (install.sh + harness registration) →
  `05cbaf3` (proxy_country) → `a19054d` (HTTP_TIMEOUT) → `50b0e03` (arg validation) →
  `fd72e55` (web_session) → `361961e` (CACHE_TTL) → `current` (dual-run emulated
  search/crawl/extract + harness-key web_extract; commit subject "Dual-run emulated
  search/crawl/extract via Scrappey + LLM web_extract").

## Round five — edge-case hardening (this session)

Line/branch coverage was already 100%, so "improve coverage" was treated as
scenario and assertion strength: measure mutation score, probe behaviour
directly, fix what the probes exposed. Six real defects found and fixed:

1. **`llm.summarize` leaked a raw `KeyError`** when a provider returned a 200
   with `{"choices": {...}}` (object instead of list): `(choices or [{}])[0]`
   raised outside the `ProviderError` contract that the emulation gate and the
   MCP error path rely on, so the emulated `web_extract` call would crash
   instead of returning JSON. Payload shape is now validated explicitly
   (malformed payload vs empty completion).
2. **Duplicate `PROVIDER_PRIORITY` names were tried twice** — the router built
   its order verbatim, so `PROVIDER_PRIORITY=exa,exa` made a failing request
   bill the same provider twice. Order now dedupes on first occurrence.
3. **Non-finite numeric env values were accepted**: `CACHE_TTL=nan` silently
   disabled the cache, `CACHE_TTL=inf` made entries never expire, and
   `HTTP_TIMEOUT=nan` produced a poisoned httpx timeout. Both now raise at
   startup.
4. **`_html_to_text` block stripping was quadratic**: 100 KB of unterminated
   `<script>` openers cost ~1 s of event-loop time, so a 1 MB hostile page
   would stall every tool for tens of seconds. Replaced with a linear
   opener/closer scan; a 50k-case differential fuzz against the original regex
   proves byte-identical output, and 8000 openers now take ~1 ms. `<[^>]+>`
   became `<[^<>]+>` for the same reason (a tag cannot contain `<<`).
5. **`_is_skippable` missed assets with a trailing slash** (`/report.pdf/`),
   so crawls fetched and billed them.
6. **A truncated SERP lost its final result** (response cut mid-`<li>`), and a
   missing `</li>` swallowed every later result. `_BingSERPParser.close()` now
   flushes the in-flight result and harvests a partial title/snippet; a
   mid-`<p>` truncation keeps the partial snippet.

New tests (296 → 348): real-stdio end-to-end suite
(`tests/test_stdio_integration.py` — `main()` env wiring incl. `.env` override
safety, transport, `tools/list` schema publication without `force`, playbook
resource read, lifespan teardown), per-tag block stripping (7 tags, case
insensitive), block-strip linearity + reference-regex equivalence, redirect
decoding dedupe, truncated title/snippet flushing, non-ASCII query encoding,
multibyte `EXTRACT_MAX_CHARS` truncation at/over the boundary, cache-key
separation for `js_render`/`premium_proxy`/`proxy_country`/`session`, duplicate
priority collapse, non-finite env rejection, and inclusive tool bounds
(`max_results` 1/50, `max_pages` 1/100, `max_depth` 1/10).

## Round six — provider response hardening (this session)

Coverage was again already at 100%, so the audit probed a different fault
class: **200 responses whose body does not match the adapter's expected
shape**. Every adapter assumed list/dict/string fields and crashed with
`TypeError`/`AttributeError`/`KeyError`/`JSONDecodeError` — none of which the
router fails over on (it handles `ProviderError`/`httpx.HTTPError`), so one
misbehaving provider took the whole tool call down instead of degrading to the
next candidate. That is exactly what providers emit during partial outages,
API deprecations, and interstitial "verify you are human" pages.

- `BaseProvider._request_json` now rejects non-JSON bodies and non-object JSON
  roots as `ProviderError`.
- New shared guards on `BaseProvider`: `_items` (list of objects), `_object`,
  and `_text` (string) — absent/`None` is an empty value, any other shape is a
  provider fault. Applied across Exa, Firecrawl, Tavily, TinyFish, Serper and
  Scrappey; ZenRows `extract` (which bypasses `_request_json`) wraps its own
  `resp.json()`.
- Firecrawl's crawl poll loop advanced `elapsed += poll_interval`, so
  `poll_interval=0` looped forever with no result. It is now bounded by a
  wall-clock deadline.
- End-to-end: a shape-invalid provider now fails over, and when every
  candidate is malformed the tool returns the aggregated JSON error (covered
  by a test through the full chain 200 → `ProviderError` → router → MCP
  response).

New tests (348 → 383): non-JSON and edge-shape responses for every adapter,
the absent-vs-malformed-vs-wrong-scalar distinction, Firecrawl zero-interval
regression, transport-failure failover
(`ConnectError`/`ReadTimeout`/`RemoteProtocolError`), real-adapter malformed
failover, protocol-level errors (unknown tool, missing/typed arguments,
unknown resource URI), `_unredirect` idempotency, crawl max-depth invariant,
and empty-string API keys treated as unconfigured.

## Since the previous handover (Oct 2026 session)

- **`_preferred` is now capability-aware (bugfix, test-discovered)**: with
  `DEFAULT_PROVIDER=scrappey`, every call was hard-pinned to Scrappey — the
  router's `preferred` semantics force the provider, so a configured real
  search/crawl/extract provider never got a chance and the emulation gate
  always fired even when a paid provider was available. Now the default
  provider is pinned only for capabilities it actually serves; otherwise
  the router auto-selects (real provider wins, gate only fires when nothing
  can serve the capability). An explicit `provider=` argument still pins.
- **Test suite expanded 138 → 231**: direct `llm.py` tests (backend
  resolution, thinking-off retry fallback, every error path, client
  lifecycle), router edges (cache cap eviction, expired-entry cleanup,
  HTTPError failover, non-caught exception propagation, disabled preferred),
  Scrappey request-body details (proxy_country/session/innerText fallback,
  browser html mode, screenshot selector + failures), Firecrawl crawl
  states/extract/timeout, Jina key requirements + parse edges, emulated
  search/crawl/extract edge cases (cycles, www/relative dedupe, scheme
  switch, fragment dedup, parser tolerance), server gate error paths,
  ENABLED_TOOLS env, playbook fallback, model to_dict, and the
  real-provider-wins scenario.
- **Test suite expanded 231 → 273 + two adapter fixes (test-discovered)**:
  - Exa: the API returns `highlights` as a list; the adapter assigned it
    verbatim to the string-typed `snippet` (list `[:500]` cap silently did
    nothing). Now flattened to text before the 500-char cap.
  - Gate eligibility is a deny-list of caller errors: `web_search/web_crawl/
    web_extract` fire the emulation gate whenever the environment cannot
    serve the call (no provider supports it OR all candidates failed — the
    keyless-Jina-always-fails path is the real-world case) but surface
    caller mistakes verbatim (unknown/disabled provider, unsupported pin,
    unknown tool). Previously a typo'd `provider=` returned a misleading
    "no provider configured" explanation, and (intermediate state) the gate
    was wrongly suppressed in the standard Scrappey-only deployment.
  - New coverage: every adapter's optional-param request bodies and failure
    paths (Exa fetch/params, Tavily params, TinyFish params/error/cap,
    Serper geo/empty, ZenRows extract/proxy_country, Scrappey default
    bodies), router cache semantics (failure never cached, format-keyed,
    key stability, available() ordering), explanation payload exact
    contents, extract truncation boundary, search-result cap, LLM 401
    no-retry, schema defaults/requireds, invalid session op, configure_
    logging wiring, PROVIDER_PRIORITY env.

- **Emulated `web_search`/`web_crawl`/`web_extract` with a dual-run trigger** —
  Scrappey has no search/crawl/extract endpoints, so when it is the only configured provider the
  tools are now served by gated emulation (`emulation.py`):
  `web_search` loads Bing's results page in **full-browser mode** (€1/1K, ~20–40s)
  and parses it into unified `SearchResult`s (Bing `/ck/a` click-tracking URLs
  decoded to their real destinations); `web_crawl` runs a
  same-host BFS with one basic-mode fetch per page; `web_extract` does one
  Scrappey fetch (basic mode by default, raw HTML stripped to text locally;
  `js_render=true` for browser mode + Scrappey markdown) plus one LLM call
  (`llm.py`: `OPENROUTER_API_KEY` or `HF_TOKEN`, thinking requested off
  best-effort, `EXTRACT_MODEL`/`EXTRACT_MAX_CHARS` env) that reduces the page
  to clean Markdown. The first call **returns an
  explanation** (mechanics + drawbacks) and does **not** execute; only a follow-up
  call with a `force=true` argument runs it. The flag is deliberately **not in the
  published schemas** — it is stripped post-registration from `tools/list`
  (`_hide_force_flags` in server.py) and revealed only by the first-call
  explanation. Real search/crawl/extract providers are always preferred when configured.
  Live-verified (2026-10): gate + force through the real Scrappey API and the
  real harness OpenRouter key (example.com → clean markdown; thinking-off
  param accepted on first attempt).

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
- `mcp-server/src/scrape_n_search/emulation.py` — Scrappey-emulated search/crawl/extract (Bing SERP parse, BFS crawl, fetch+LLM extract) + dual-run explanation payloads
- `mcp-server/src/scrape_n_search/llm.py` — minimal OpenAI-compatible chat client for emulated extract (harness-key resolution, thinking-off best-effort)
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
- **httpx INFO logging leaks the Scrappey API key**: with default `LOG_LEVEL=INFO`,
  httpx logs every request URL, and the Scrappey key rides in the query string
  (`POST https://publisher.scrappey.com/api/v1?key=…`) — observed live. Key is
  gitignored and only in `.env`, but the log line exposes it; consider filtering
  httpx logs or `LOG_LEVEL=WARNING` on shared machines.
- **Emulated search (live-verified 2026-10)**: Bing serves every organic result as a
  `/ck/a` click-tracking redirect — the server decodes the base64 `u=` param to the
  real destination URL. **Basic mode is unusable for search**: Bing served stale
  cached result pages for *other* queries to Scrappey's rotating proxies (4/4 probe
  requests returned coherent but off-query results, identical across repeats), which
  is why emulated search uses full-browser mode (~30s, €1/1K). Results can include
  promoted entries and geo varies with the proxy; parsing is best-effort.
- **Emulated crawl returns raw HTML** per page (basic mode); no JS rendering, and any
  page error aborts the whole crawl (no partial results — by design, documented in the
  call-time explanation).

## Open items / next steps

- **Releases/packaging**: done — MIT license, CI + release workflows, v0.1.0 +
  v0.1.1 published, **PyPI live** (`scrape-n-search-mcp`, trusted publishing verified,
  fresh-venv `pip install` + server boot checked; uvx not installed on this machine,
  verify `uvx scrape-n-search-mcp` elsewhere). Future releases: bump
  pyproject.toml + `__init__.py` `__version__` in one commit, then
  `git tag vX.Y.Z && git push origin vX.Y.Z`. One-line installer
  (`curl | bash`) live and pipe-safe (`--ref` pins tags).
- Cross-request session reuse (cookies/login state) is blocked by Scrappey's current
  request lifecycle — re-test when the vendor changes behavior.
- Scrappey's own GPT-4 "AI extraction" direct-API feature is still not exposed via
  the MCP (the emulated `web_extract` is fetch + harness-key LLM summary, not
  Scrappey's extractor) — could be added via the direct API.
- `web_crawl` is served by Firecrawl when configured and by the gated Scrappey
  emulation otherwise; `web_extract` is served by Firecrawl/ZenRows when configured
  and by the gated fetch+LLM emulation otherwise (needs `OPENROUTER_API_KEY`/`HF_TOKEN`).
- The `main`-branch OMP JSON schema is stale (rejects even the working `hf-vision` entry);
  runtime validation is permissive — don't "fix" the config to satisfy that schema.

## Note

The CMP 170HX / Reddit / AliExpress research performed during a previous session was a
**test of tool calling** — it is not part of the deliverable and should be ignored.
