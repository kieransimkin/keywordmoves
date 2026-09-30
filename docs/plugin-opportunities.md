# Plugin opportunities

Research checked 30 September 2026. These are candidates, not claims that an
integration is already authorised, authenticated, or implemented. First-party
signals from different platforms must remain separate rather than being merged
into a fictional universal popularity score.

| Priority | Proposed plugin | Discovery or analysis value | Access and evidence limits |
| --- | --- | --- | --- |
| 1 | Google Search Console | Owned-site queries, pages, clicks, impressions, CTR and position. Strongest first-party evidence for phrases already surfacing a site. | OAuth and property permission required. The API returns top rows under internal limits and is not a complete census. |
| 1 | Google Ads Keyword Planner | Keyword and URL seeds, related ideas, historical metrics and forecasts. Useful companion to Trends for volume estimates. | Google Ads account, OAuth and developer-token access required; services are rate-limited and historical metrics refresh monthly. |
| 2 | Bing Webmaster Tools | Registered-site keyword and traffic details from a second search engine. | Account/site access required. Build on the REST API; legacy SOAP and POX retire on 31 August 2026. |
| 2 | YouTube discovery | Search result landscape for a phrase, narrowed by type, topic, region and date; optionally combine with owned-channel analytics in a separate plugin. | API key or OAuth and quota required. Search-result counts and rankings are not native search-volume figures. |
| 2 | Pinterest Trends | Top growing keywords, growth rates and a normalised weekly time series; Pinterest also exposes related terms and country keyword metrics. | Trends API availability is limited to agencies, enterprise clients and partners, and returns current-date results only. Keep Pinterest evidence platform-specific. |
| 2 | TikTok Keyword Planner / Creative Centre import | Native search-ad keyword ideas, volume, competition and public trend/keyword-insight exports or captured records. | The Keyword Planner is an Ads Manager surface with country availability. No unsupported private endpoint scraping: begin with a documented manual/export importer unless TikTok grants an API. |
| 3 | Wikimedia Pageviews | Public page-interest time series for entities and topics, useful as a corroborating attention proxy. | Pageviews are not web-search demand. Entity resolution and redirects need explicit evidence. |
| 3 | Reddit discussion search | Candidate phrases, communities and dated discussion counts for audience-language research. | API terms, authentication and rate limits apply. Discussion frequency is not search popularity or endorsement. |
| 3 | SERP snapshot provider | Current result types, competing pages, titles, related questions and lexical patterns. | Prefer an authorised API with stable terms. Do not scrape access gates or present result counts as precise demand. |
| 4 | Local embeddings / clustering | Group candidates, detect duplicates, map seeds to related concepts and compare multiple text libraries without sending text away. | A separate LLM or embedding plugin should expose model identity and similarity method; similarity is not demand evidence. |

## Source notes

- Google Trends API alpha: <https://developers.google.com/search/apis/trends>
- Google Search Console Search Analytics: <https://developers.google.com/webmaster-tools/v1/how-tos/search_analytics>
- Google Ads Keyword Planning: <https://developers.google.com/google-ads/api/docs/keyword-planning/overview>
- Bing Webmaster API: <https://learn.microsoft.com/en-us/bingwebmaster/>
- YouTube Data API search: <https://developers.google.com/youtube/v3/docs/search/list>
- Pinterest Trends API: <https://developers.pinterest.com/docs/analytics-and-reports/trends/>
- TikTok Keyword Planner: <https://ads.tiktok.com/help/article/about-the-keyword-planner-tool-in-tiktok-as-manager>
- Hugging Face Qwen2.5 0.5B Instruct: <https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct>

## Recommended build order

1. Search Console, because it provides Kieran's own query/page evidence.
2. Google Ads Keyword Planner, because it adds ideas and volume-style metrics
   that Google Trends deliberately does not supply.
3. Bing Webmaster, then YouTube, to widen first-party search surfaces without
   pretending that their metrics are interchangeable.
4. Pinterest and TikTok import/API adapters only when the relevant account or
   partner access is available and the platform contract is clear.
