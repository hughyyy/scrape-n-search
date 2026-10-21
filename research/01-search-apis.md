# Search + Fetch APIs for agents

All of these run fetches on the provider's servers, so the client IP (VPN) is irrelevant
to target sites. Preferred for agent tool use: agent-native response format, MCP support,
combined search + content extraction.

## TinyFish — tinyfish.ai

- **Search**: FREE, 30 req/min. **Fetch**: FREE, 150 urls/min. No card, no subscription,
  no monthly minimum.
- **Agent** $0.016/step, **Browser** $0.002/min — metered against a prepaid **Wallet**.
  Search and Fetch stay free at any wallet balance.
- Fetch runs a real full-browser render and returns clean markdown/JSON/HTML; residential
  proxies + anti-bot handled at the infrastructure layer.
- MCP server, CLI, SDKs.
- **Risk**: newer vendor, shorter track record. Claims "Search and Fetch free at any wallet
  balance" — confirm it holds before depending on it at scale.

## Tavily — tavily.com

- **Researcher (Free)**: 1,000 API credits/mo, no credit card required. Credits reset
  monthly, do not roll over.
- **Pay As You Go**: $0.008/credit, cancel anytime, no monthly commitment — prepaid-friendly.
- Agent-native search + extract (content fetch). Deep LangChain/LlamaIndex integrations, MCP.
- **Risk**: Nebius announced acquisition of Tavily (Feb 2026); pricing/roadmap stability
  is an open question for long-term dependency.

## Exa — exa.ai

- **$20 free credits on signup** (~2,800 searches) + **$10 in credits monthly** on the
  free tier.
- Pure **pay-as-you-go**: no subscription, no minimum — load credits and draw down.
- Search $7/1k (up to 10 results), Contents $1/1k pages, Answer $5/1k, Deep Search $12–15/1k.
- Neural/embedding-based semantic search (powers Cursor @web). MCP server.
- Best for research/RAG/coding-agent search where semantic matching matters.

## Firecrawl — firecrawl.dev

- **Free**: 1,000 credits/mo, no card.
- **No PAYG plan**: beyond free it is subscription (Hobby $19/mo → 5,000 credits).
- Scrape 1 credit/page, Search 2 credits / 10 results, Map 1 credit/call, Interact
  2 credits/browser-min.
- Scrape/Crawl/Map/Search; MCP (`npx -y firecrawl-mcp`); open source under AGPL-3.0.

## Serper — serper.dev (search only)

- **2,500 free queries**, no card; then from **$0.30/1k** (Starter $1/1k, $0.30 at volume).
- Google SERP JSON only — no content extraction/page fetch.
- Lowest cost-per-query for raw Google results; pair with Jina Reader or a fetch API.

## Brave Search API — brave.com/search/api (search only)

- **No free tier for new users** (removed). Credit-based billing: $5/mo ≈ 1,000 queries,
  then billed at $5/1k.
- Independent index (no Google/Bing dependency); privacy controls; MCP server.
- Search only — no fetch endpoint.

## Jina Reader — jina.ai/reader (zero-cost fallback)

- URL-prefix pattern: prefix `https://r.jina.ai/` to a URL for markdown; search via
  `https://s.jina.ai/`.
- Free without an API key for basic use; new keys get 10M free tokens; charges then based
  on content length.
- **Does not bypass anti-bot** — returns an error when blocked.
- **Status (Oct 2026)**: `s.jina.ai` search now requires an API key (401 without);
  keyless fetch via `r.jina.ai` still works.
