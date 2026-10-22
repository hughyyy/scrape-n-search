# Scrappey common-actions playbook

Guidance for agents using the scrape-n-search MCP tools to read the web through
Scrappey. This playbook is exposed as the `scrappey://playbook` MCP resource.

## Tools

- `web_fetch` — fetch a page. `js_render=false` = Scrappey basic (€0.10/1K, raw HTML);
  `js_render=true` = full browser (€1/1K, JS rendering, markdown).
- `web_browser_actions` — run a sequence of browser actions on a page (click, scroll,
  type, execute_js, wait…) and return the page content plus any `execute_js` results.
- `web_screenshot` — capture a screenshot (base64 PNG).
- `web_session` — create/destroy Scrappey browser sessions; pass the name to the other
  tools via `session=`. Note: Scrappey currently destroys a named session once a
  request completes, so cookies/state do not persist across calls (verified 2026-10).
- `web_search` / `web_crawl` / `web_extract` — when no real search/crawl/extract
  provider is configured, the first call returns an explanation of the gated
  emulation (Bing SERP parse / same-host BFS over basic-mode fetches / fetch +
  LLM summary) and its drawbacks; re-call with the `force` argument described
  in that explanation to execute. `web_extract` needs an LLM key
  (`OPENROUTER_API_KEY` or `HF_TOKEN`) reachable by the server — the LLM call
  bills the harness LLM account, not the Scrappey balance.

## General rules

- **Prefer `old.reddit.com` for Reddit.** New-reddit search returns spam/irrelevant
  results; `old.reddit.com` is server-rendered and works reliably in browser mode.
  - Search: `https://old.reddit.com/search?q=<query>&sort=relevance&t=year`
  - Thread: `https://old.reddit.com/r/<sub>/comments/<id>/<slug>/`
- **Bing works in basic mode** (`js_render=false`) for simple searches:
  `https://www.bing.com/search?q=<query>`. DuckDuckGo's html endpoint challenges
  basic mode (bot CAPTCHA) — use browser mode or Bing instead.
- **AliExpress descriptions are lazy-loaded and collapsed** behind a "View more"
  toggle. To read the full item description, use `web_browser_actions` to scroll
  (trigger lazy-load), click "View more", wait, then extract.

## Browser-action recipes

### Read the full AliExpress item description
```json
{
  "url": "https://www.aliexpress.com/item/<ITEM_ID>.html",
  "actions": [
    {"type": "scroll", "repeat": 3, "delayMs": 800},
    {"type": "wait", "wait": 2},
    {"type": "execute_js", "code": "(() => { const b=[...document.querySelectorAll('button,span,a,div')].find(e=>e.textContent.trim().toLowerCase().includes('view more')); if(b){b.click(); return 'clicked';} return 'notfound'; })()"},
    {"type": "wait", "wait": 3},
    {"type": "execute_js", "code": "document.body.innerText"}
  ]
}
```
The last `execute_js` result appears in `javascript_return` in the response.

> **Important**: `javascript_return` is only populated when `format` is `"html"`.
> The markdown filter (`filter:["markdown"]`) suppresses it. If you need
> `execute_js` results, call `web_browser_actions` with `format: "html"`.

### Extract a specific element's text
```json
{"type": "execute_js", "code": "document.querySelector('#price')?.innerText || 'not found'"}
```

### Wait for lazy-loaded content
```json
{"type": "wait_for_selector", "cssSelector": ".product-description", "timeout": 30000}
```

### Click a button / expand an accordion
```json
{"type": "click", "cssSelector": "button.more", "wait": 2, "waitForSelector": ".expanded"}
```

### Scroll to trigger infinite scroll / lazy load
```json
{"type": "scroll", "repeat": 5, "delayMs": 500}
```

### Type into a form field
```json
{"type": "type", "cssSelector": "#search", "text": "query", "wait": 1}
```

### Press a key
```json
{"type": "keyboard", "value": "enter", "wait": 2}
```

## Action reference

Supported action types (Scrappey): `click`, `type`, `goto`, `wait`,
`wait_for_selector`, `wait_for_function`, `wait_for_load_state`, `wait_for_cookie`,
`execute_js`, `scroll`, `hover`, `keyboard`, `dropdown`.

Common params: `type` (required), `when` (`beforelaunch`|`beforeload`|`afterload`,
default `afterload`), `ignoreErrors`, `timeout`.

- `click` — cssSelector, wait, waitForSelector, direct
- `type` — cssSelector, text, wait, direct
- `execute_js` — code, dontReturnValue
- `scroll` — cssSelector, repeat, delayMs
- `wait` — wait (seconds)
- `wait_for_selector` — cssSelector, timeout
- `keyboard` — value (`tab`|`enter`|`space`|`arrowdown`|`arrowup`|`arrowleft`|`arrowright`|`backspace`|`clear`), cssSelector, wait

## Notes

- Browser actions run in full browser mode (€1/1K).
- `execute_js` results are returned in the response's `javascript_return` array.
- After actions run, the page content is also returned (markdown by default).
- Only Scrappey supports browser actions; the tool errors if no Scrappey key is set.
