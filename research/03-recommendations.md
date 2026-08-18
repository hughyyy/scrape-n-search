# Recommendation & next steps

## Situation

Many web requests from a VPN IP are rejected. Need low-cost, credit/prepaid (ideally
no monthly subscription) web-search + scraping APIs for agent tool use. Free trial
preferable.

## Decision

Use server-side search/fetch APIs — the provider fetches from its own IP pools, so the
VPN IP never touches the target site.

### Recommended stack

1. **Search + content fetch (free to start, no card):**
   - **TinyFish** — Search & Fetch free at any wallet balance; real-browser render,
     clean markdown; MCP. Fund the prepaid wallet only if you need Agent/Browser.
   - **Tavily** — fallback/mature option: 1,000 free credits/mo, then PAYG
     $0.008/credit with no monthly commitment.

2. **Scraping hard / anti-bot sites:**
   - **ZenRows** — 5,000 free credits/mo (no card); credit-based, only successful
     requests charged; residential premium proxies for protected sites; top-ups.

3. **Pure prepaid no-subscription at scale:**
   - **Exa** — $20 free + $10/mo free-tier credits; load credits, no subscription/minimum.

## Whitelist of acceptable (non-essential) alternatives

- **Serper** — 2,500 free queries, from $0.30/1k, Google SERP only (pair with a fetch API).
- **Jina Reader** — zero-key URL→markdown fallback; cannot bypass anti-bot.
- **Firecrawl** — 1,000 free credits/mo, but subscription-only beyond free (no PAYG).

## Tradeoffs / risks

- **TinyFish** — newer vendor; verify the "free at any wallet balance" claim holds before
  depending on it at scale.
- **Tavily** — Nebius acquisition (Feb 2026) raises pricing-stability questions.
- **Anti-bot success varies per site** — residential-proxy routing (ZenRows premium,
  TinyFish, ScraperAPI) wins on hard targets; must test against the actual intended sites.

## Next steps

1. Sign up for TinyFish + ZenRows (both free, no card).
2. Point both at the specific sites that currently reject the VPN IP; compare fetch
   success rates per site.
3. Pick the winner; wire its MCP server into the agent.
4. If a no-subscription, purely prepaid account is required, load Exa credits instead.
