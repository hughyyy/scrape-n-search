# scrape-n-search

Research: low-cost, credit/prepaid web-search and site-scraping APIs for AI-agent tool
use, chosen so requests are not rejected based on a VPN IP.

## Problem

Fetching/searching from a machine behind a VPN gets rejected because target sites block
the VPN IP. The fix is **server-side APIs**: the provider's servers perform the fetch
from their own IP pools (datacenter, rotating, or residential). The user's VPN IP never
contacts the target site. The only outbound request is an authenticated HTTPS call to the
API endpoint itself, which target sites do not block.

Caveat: the VPN must permit plain outbound 443 to the API host (normal for any VPN).

## Contents

- [`research/01-search-apis.md`](research/01-search-apis.md) — search + fetch APIs
- [`research/02-scraping-apis.md`](research/02-scraping-apis.md) — scraping-specific APIs
- [`research/03-recommendations.md`](research/03-recommendations.md) — recommendation and next steps

## Verdict (TL;DR)

| Need | Pick | Why |
|---|---|---|
| Search + fetch, free, no card | **TinyFish** or **Tavily** | Search+Fetch free (TinyFish); 1,000 free credits/mo then $0.008/credit PAYG (Tavily) |
| Scraping hard / anti-bot sites | **ZenRows** | 5,000 free credits/mo, credit-based, only successful requests charged, residential proxies |
| Pure prepaid, no subscription | **Exa** | $20 free + $10/mo, load credits, no subscription/minimum |

All facts verified against primary-source pricing pages on 2026-08-18. See each file for
full detail and sources.
