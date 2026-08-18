# Scraping-specific APIs

Use these when search APIs' built-in fetch is not enough, or you need to defeat
anti-bot / JS-heavy pages. All handle the fetch on their own infrastructure (rotating or
residential proxies), so the client VPN IP is irrelevant.

## ZenRows — zenrows.com (recommended)

- **Free**: 5,000 credits/mo, no card required.
- **Credit-based**: only successful requests are charged; 404/410 count as successful
  (free). One credit = one standard successful page.
- Cost ladder: standard 1 credit, JS rendering 5, premium (residential) proxy 10,
  both together 25, residential bandwidth 25,000/GB.
- Paid plans from $19/mo (45,000 credits); **top-ups** allowed (up to 4×/period) and
  unused top-up credits roll over.
- MCP server, CLI, SDKs (Claude Code, Cursor, Codex, Windsurf, Gemini CLI).
- Best credit/prepaid fit for scraping.

## ScraperAPI — scraperapi.com

- **7-day trial, 5,000 API credits, no credit card required.**
- Paid plans are subscriptions: Hobby $49/mo (100k credits), Startup $149/mo (1M).
- **Pay-as-you-go** only appears at higher tiers (Scaling $475/mo, Professional $975/mo,
  Advanced $1,975/mo) → not a low-cost prepaid option.
- JS rendering, premium proxies, CAPTCHA/anti-bot handling included.

## ScrapingBee — scrapingbee.com

- **Subscription-only**: Hobby $19.99/mo (75k credits) up through Enterprise.
- Free trial credits exist; no prepaid/pay-as-you-go model.
- JS rendering, rotating + premium proxies, geotargeting.

## Key comparison

| Tool | Free tier | Prepaid / PAYG | Lowest entry |
|---|---|---|---|
| ZenRows | 5,000 credits/mo (no card) | Yes — credit-based, top-ups | Free / $19/mo |
| ScraperAPI | 7-day, 5,000 credits (no card) | PAYG only on $475+/mo | $49/mo |
| ScrapingBee | trial credits | No | $19.99/mo |
