# Online keyword discovery and analysis

Research date: **2 October 2026**. Integration baseline:
`23e6959bbaabfe4e34d5c073afc0529bc004b5f4`.

This is a defined set of **18 new adapters**, not a claim to support every online
SEO product or every endpoint offered by these providers. Twelve adapters use
provider-documented APIs; three use explicitly experimental browser suggestion
endpoints; one handles reviewed public HTML; two import user-exported reports.
The existing `google-trends` CSV importer remains unchanged.

**Access status matters.** The request/response contracts were researched and
covered by offline tests. Paid accounts, live authenticated API requests and the
three browser endpoints were not exercised successfully in the build environment.
Ubersuggest and AnswerThePublic do not have live-query implementations in this
patch. Their import adapters must not be described as working website scrapers.

## Installation

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

The extra installs HTTPX and Beautiful Soup. Core installs remain dependency-free;
listing plugins does not import HTTPX, Beautiful Soup or any model runtime. No
LLM, PyTorch installation or API key for OpenAI is needed for these sources.

Network operations use repeated `--keyword "phrase"` arguments, not `--input`.
Report imports use `--input`. Online sources do not use the generative runtime
flags `--llm`, `--model` or `llm_` options. Each provider is selected explicitly;
there is no automatic paid-provider or website fallback.

## Provider and access matrix

| Plugin | Operations implemented | Route and access requirements | Evidence meaning |
| --- | --- | --- | --- |
| `google-ads` | `ideas`, `metrics` | Google Ads REST planning API; OAuth access token, developer token and authorised customer ID | Planning estimates, paid competition and account-currency bids |
| `search-console` | `queries` | Search Console Search Analytics API; OAuth access to the named property | Observed property clicks, impressions, CTR and average position |
| `dataforseo` | `ideas`, `suggestions`, `metrics` | DataForSEO Labs Google live endpoints; API login/password and credits | Provider keyword ideas, volume, CPC and difficulty estimates |
| `semrush` | `related`, `broad-match`, `metrics` | Analytics API v3 keyword reports; API key and suitable account/units | Database-specific keyword volume, CPC and paid competition |
| `ahrefs` | `matching-terms`, `questions` | API v3 Keywords Explorer matching-terms endpoint; bearer key and API access | Country-specific volume, organic difficulty and CPC in cents |
| `keywordtool` | `suggestions`, `metrics` | Keyword Tool API v2; API-enabled plan/key | Multi-platform suggestions and explicitly scoped volume estimates |
| `keywords-everywhere` | `metrics` | Keyword Data API; bearer key and credits | Batch historical volume, CPC and paid competition |
| `alsoasked` | `questions` | AlsoAsked synchronous Search API; API key and credits | Nested People Also Ask relationships |
| `serpapi` | `autocomplete`, `related-searches`, `questions` | SerpApi Google endpoints; API key | Search-feature phrases and returned ordering |
| `brave-suggest` | `suggestions` | Brave Suggest API; subscription token with access | Search suggestions and ordering |
| `datamuse` | `related`, `synonyms`, `suggestions` | Public documented word API; currently no key in this adapter | Lexical association, not demand |
| `wikipedia` | `suggestions` | MediaWiki OpenSearch API | Encyclopaedia title/topic suggestions, not demand |
| `google-autocomplete` | `suggestions` | Experimental browser-client endpoint; `allow_unofficial=true` and robots check | Unofficial autocomplete ordering only |
| `bing-autocomplete` | `suggestions` | Experimental browser-client endpoint; `allow_unofficial=true` and robots check | Unofficial autocomplete ordering only |
| `duckduckgo-autocomplete` | `suggestions` | Experimental browser-client endpoint; `allow_unofficial=true` and robots check | Unofficial autocomplete ordering only |
| `website-keywords` | `query`, `import-html` | Reviewed public URL template and CSS selector, or saved rendered HTML | Literal selected DOM text |
| `ubersuggest` | `import-csv`, `import-html` | User-exported report; **no live query** | Explicitly mapped report metrics or selected DOM text |
| `answerthepublic` | `import-csv`, `import-html` | User-exported report; **no live query** | Explicitly mapped report metrics or selected DOM text |

An "official API" capability here means a documented API offered by that
provider. SerpApi, DataForSEO and other aggregators are not first-party Google
APIs. API access, quotas, retention rights and costs depend on the provider and
account. Check the linked provider documentation before enabling production use.

## Credentials

Use environment-based secret injection wherever possible. The following explicit
options override their environment variable for that provider. A blank explicit
credential is an error; it does not fall back to a different account silently.
A `.env` file is not loaded automatically.

| Provider | Environment variable(s) | Explicit option(s) |
| --- | --- | --- |
| Google Ads | `GOOGLE_ADS_ACCESS_TOKEN`, `GOOGLE_ADS_DEVELOPER_TOKEN` | `access_token`, `developer_token` |
| Search Console | `SEARCH_CONSOLE_ACCESS_TOKEN` | `access_token` |
| DataForSEO | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | `login`, `password` |
| Semrush | `SEMRUSH_API_KEY` | `api_key` |
| Ahrefs | `AHREFS_API_KEY` | `api_key` |
| Keyword Tool | `KEYWORDTOOL_API_KEY` | `api_key` |
| Keywords Everywhere | `KEYWORDS_EVERYWHERE_API_KEY` | `api_key` |
| AlsoAsked | `ALSOASKED_API_KEY` | `api_key` |
| SerpApi | `SERPAPI_API_KEY` | `api_key` |
| Brave | `BRAVE_SEARCH_API_KEY` | `api_key` |

For example, `$env:SEMRUSH_API_KEY = "YOUR_KEY"` in PowerShell or
`export SEMRUSH_API_KEY="YOUR_KEY"` in Bash configures the current session.
Both literal assignments and `--option api_key=...` may enter shell history;
command-line keys can also appear in process listings. Never commit real keys.

Google access tokens must be obtained and renewed through the normal authorised
OAuth flow outside this plugin. This patch does not create OAuth clients, store
refresh tokens, grant permissions, or create/change advertising campaigns.

Keys are not copied into result metadata or normal provider-error messages.
Semrush and SerpApi require query-string credentials in the implemented routes;
external HTTP debugging, proxies or application logging can expose those URLs.
Request options themselves contain secrets for Python callers and must not be
logged. Proxy/environment HTTP configuration is not inherited (`trust_env=False`).

## Common limits and result semantics

All network adapters support these options through `--option NAME=VALUE`:

| Option | Default | Effect |
| --- | --- | --- |
| `limit` | `50` | Maximum candidates emitted, 1–1000. Passed upstream where supported; **not a universal cost cap**. |
| `max_requests` | `5` | Maximum HTTP calls in one run, 1–20, including robots requests. |
| `timeout` | `30` | HTTP timeout setting in seconds, 1–120; not a wall-clock deadline for the whole run. |
| `max_response_bytes` | `2000000` | Maximum decoded response size, 1024–10000000 bytes; also bounds import files. |
| `min_interval` | `1` | Minimum seconds between requests inside a run, 0–60; cannot reduce the provider's minimum. |

The API request interval floor is one second, four seconds for Keyword Tool, and
two seconds for experimental browser endpoints. Robots crawl-delay/request-rate
can increase the interval. These are **per-run safeguards**, not global rate
coordination: concurrent processes and repeated short runs must still comply
with account-wide limits. No cache, automatic retry or expansive alphabet/question
fan-out is performed. A failed or timed-out paid request may still consume credits.

HTTP redirects, authentication failures, rate limiting and unexpected response
formats stop the operation. No automatic retry is made for 429 or billable POSTs.
TLS verification remains enabled. A response-size failure is not silently
truncated into apparently valid data. `max_requests` exhaustion is an error,
not a claim that the remaining pages were empty.

`KeywordCandidate.score` is deliberately `null`. Instead, `KeywordEvidence`
records provider-specific metrics and units. Output distinguishes:

- Search suggestions, question relationships and lexical/topic suggestions.
- Provider estimates of monthly searches, costs and competition.
- First-party performance for an authorised Search Console property.

Missing values stay `null`; reported zero stays `0`. Paid-ad competition is not
organic keyword difficulty. A returned-order value is not search popularity or
organic rank. Metrics from different providers, dates, platforms and geographies
must not be merged as though they were one comparable score.

Network evidence `observed_at` is the UTC retrieval date, **not the period that
historical metrics measure**. Results also carry `retrieved_at`, `request_count`,
`rows_received`, `output_truncated` and a completeness note. Provider periods,
monthly breakdowns or update times are retained where available. Imports require
an explicit report observation date. Provider ordering and scoped duplicate
phrases are retained rather than collapsing different observations together.

## Google Ads: planning ideas and historical metrics

```powershell
keywordmoves run google-ads --operation ideas `
  --keyword "independent music" `
  --option customer_id=1234567890 `
  --option location_codes=2826 `
  --option language_id=1000 `
  --option limit=20
```

Use `--operation metrics` and repeated keywords for existing phrases.
`customer_id` is required; hyphens are accepted. Optional `login_customer_id`
sets the manager-account header. `location_codes` is required, accepts up to ten
comma-separated Google geo-target IDs; `2826` represents the UK. `language_id`
defaults to `1000` (English). The network is `GOOGLE_SEARCH`.

The default `api_version=v25` follows the reviewed release line; it can be
changed explicitly when the API is upgraded. Ideas accept up to 20 seeds;
historical metrics accept up to 100 in this adapter. Ideas support `page_size`
(default `limit`, at most 1000), `max_pages` (default 1, at most 10), and
`page_token`. `next_page_token` is returned when the provider supplies it.
Historical metrics perform one request. Local output limits can omit results
when more keyword seeds than the limit are supplied.

Endpoints are `POST /v25/customers/{customer}:generateKeywordIdeas` and
`:generateKeywordHistoricalMetrics` on `googleads.googleapis.com`.
Bids remain in **account-currency micros**, not assumed USD. Planning values can
aggregate close variants. This is not an exact market-wide query counter.

## Search Console: observed queries for your own property

```powershell
keywordmoves run search-console --operation queries `
  --option site_url=sc-domain:kieransimkin.co.uk `
  --option start_date=2026-09-01 --option end_date=2026-09-30 `
  --option country=gbr --option limit=100
```

`site_url`, `start_date` and `end_date` are required. The property must exactly
match one authorised for the token. Optional one `--keyword` applies a
query-contains filter, not a market-wide search. `country` is a **three-letter**
code here. `search_type` is `web` (default), `image`, `video` or `news`.
The plugin requests the `query` dimension with final data.

`page_size` defaults to `limit` and is at most 1000; `max_pages` defaults to 1
(at most 10); `start_row` defaults to 0. `next_start_row` is returned when another
page may be available. Search Console explicitly does not guarantee every row;
pagination does not remove anonymisation or internal top-row limits. Date
boundaries use **America/Los_Angeles**, not the machine's local timezone.

The request is `POST https://www.googleapis.com/webmasters/v3/sites/{encoded-property}/searchAnalytics/query`.
CTR is a fraction, not a percentage; average position is not a current rank check.

## DataForSEO Labs

```powershell
keywordmoves run dataforseo --operation suggestions `
  --keyword "independent music" --option location_code=2826 `
  --option language=en --option limit=20
```

`location_code` is required; `language=en` is the default. `ideas` and `metrics`
accept up to 100 repeated seeds, `suggestions` takes one. Each request submits
one task to `/v3/dataforseo_labs/google/{keyword_ideas|keyword_suggestions|keyword_overview}/live`.
Both envelope and task success codes are checked before accepting results.

Ideas/suggestions support `offset` (default 0); there is no automatic pagination.
`total_count` and `reported_cost` are retained when returned. CPC is USD; paid
competition and organic difficulty remain separate. Use your account's current
pricing to estimate costs, not the local `limit` alone.

## Semrush

```powershell
keywordmoves run semrush --operation related `
  --keyword "independent music" --option database=uk --option limit=20
```

One seed and an explicit `database` are required. Operations map to
`phrase_related`, `phrase_fullsearch` and `phrase_this` reports at
`https://api.semrush.com/`. The adapter requests `Ph,Nq,Cp,Co` and parses
semicolon-separated CSV with the exact declared headers. It requests one
bounded report, without automatic pagination. Error 50 means no matching data,
not verified zero searches; other API errors stop the operation. CPC is USD.
This uses the reviewed v3 Analytics contract, not an assumed v4 migration.

## Ahrefs

```powershell
keywordmoves run ahrefs --operation questions `
  --keyword "independent music" --option country=gb --option limit=20
```

Use `matching-terms` for the broader report. `country` is required; up to 20
seeds are accepted. Commas inside seeds are rejected because the API joins seeds
with commas. `match_mode=terms` is the default; `phrase` is available.
`GET /v3/keywords-explorer/matching-terms` requests
`keyword,volume,difficulty,cpc`; questions use `terms=questions`.
**CPC values are in USD cents**, and are labelled `USD_cents` without conversion.
This adapter performs one request and does not implement every Keywords Explorer
report or competitor endpoint.

## Keyword Tool: multi-platform suggestions and volume

```powershell
keywordmoves run keywordtool --operation suggestions `
  --keyword "independent music" --option platform=youtube `
  --option country=GB --option language=en --option limit=20
```

Implemented suggestion platforms:
`google`, `bing`, `youtube`, `perplexity`, `amazon`, `ebay`, `app-store`,
`play-store`, `instagram`, `twitter`, `reddit`, `pinterest`, `etsy`, `tiktok`,
`naver`, `google-trends`. All except `google-trends` support `metrics`.
These are Keyword Tool's provider routes, **not direct APIs from each platform**.

Suggestions take one seed. Metrics accept up to 100 seeds in this adapter.
Keywords are limited to 80 characters and ten words each. The default platform
is Google. The plugin uses a JSON-body API key, not a URL key. `sandbox=true`
selects `https://api.keywordtool.io/v2-sandbox` instead of production `/v2`.
`limit` trims local output; it does not cap how many keywords the provider
returns or charges for. `metrics=false` is the default for suggestions.

For Google volume:

```powershell
keywordmoves run keywordtool --operation metrics `
  --keyword "independent music" --keyword "paper plane song" `
  --option platform=google --option location_code=2826 `
  --option language=en --option currency=GBP
```

For Google/Bing, metrics require **their provider-specific `location_code`** and
use `metrics_location`/`metrics_language`. Google UK is `2826`; Bing UK is `188`.
For other platforms, metrics use required `country` and `language` instead.
Suggestions always require `country`. Google/Bing suggestions with `metrics=true`
require both the suggestion country and metric location; these scopes are kept
separate in output evidence and metadata.

`currency=USD` is the default. Google `network` defaults to `googlesearchnetwork`
(includes the partner network); use `googlesearch` to restrict it. Bing defaults
to `ownedandoperatedandsyndicatedsearch`, with `ownedandoperatedonly` and
`syndicatedsearchonly` alternatives. Bing `metrics_source=keyword_planner` is
the default; `historical_data` is available but its coverage/fields differ.
Optional `category` is sent as the provider's search vertical.

The `suggestion_type` options are platform-specific:

| Platforms | Allowed values; first is the adapter default |
| --- | --- |
| Google, Bing | `suggestions`, `questions`, `prepositions`, `related` |
| YouTube | `suggestions`, `questions`, `prepositions`, `hashtags` |
| Perplexity | `suggestions`, `questions`, `prepositions` |
| Amazon, eBay | `suggestions`, `prepositions` |
| Instagram | `hashtags`, `people` |
| Twitter | `suggestions`, `hashtags` |
| Reddit | `suggestions`, `communities`, `profiles` |
| Google Trends | `top`, `rising` |
| Other implemented platforms | `suggestions` |

A provider partial-results notice is preserved as `partial_notice` and a partial
completeness message. Missing volume does not become zero. Google Trends in
this adapter supplies related phrases only, not a Trends time-series importer.

## Keywords Everywhere

```powershell
keywordmoves run keywords-everywhere --operation metrics `
  --keyword "independent music" --keyword "paper plane song" `
  --option country=gb --option currency=gbp
```

`country` is required; currency defaults to `usd`. Up to 100 seeds are submitted
in one form-encoded `POST /v1/get_keyword_data` using repeated `kw[]` fields and
`dataSource=gkp`. CPC is labelled with the requested currency; trend and
`credits_consumed` are retained where supplied. These are historical keyword
metrics, not a claim of real-time volume.

## AlsoAsked

```powershell
keywordmoves run alsoasked --operation questions `
  --keyword "independent music" --option country=gb `
  --option language=en --option depth=2 --option timeout=120
```

The API route is `POST https://alsoaskedapi.com/v1/search` with `X-Api-Key`.
`sandbox=true` selects `sandbox.alsoaskedapi.com`. Up to five seeds, required
`country`, `language=en`, `depth=2` (or 3), and `fresh=false` are supported.
Requests explicitly use `async=false` and `notify_webhooks=false`.

The plugin walks `queries[].results` recursively, preserving the seed, parent
question and depth. It does not flatten this into a popularity score. An
incomplete request is an error; there is no status polling or resubmission.
After a timeout, inspect the provider account before retrying: a charge may have
occurred even though the CLI received no usable result. Deeper trees may use
more credits.

## SerpApi

```powershell
keywordmoves run serpapi --operation questions `
  --keyword "independent music" --option country=uk --option language=en
```

`autocomplete` uses `engine=google_autocomplete`; `related-searches` and
`questions` use `engine=google`. All use one seed, explicit `country` (`gl`),
and `language=en` (`hl`) at `https://serpapi.com/search.json`.

Only a confirmed successful search is accepted. A successful Google search can
legitimately lack a questions or related-searches block; missing autocomplete
schema is an error. These operations parse one response, not recursive PAA
expansion or additional organic-result pages. SerpApi is the API provider, not
Google's first-party Search Console or Ads service.

## Brave Suggest

```powershell
keywordmoves run brave-suggest --operation suggestions `
  --keyword "independent music" --option country=GB --option limit=10
```

`GET https://api.search.brave.com/res/v1/suggest/search` uses an
`X-Subscription-Token`, one seed, required country and `language=en`.
The upstream count is capped at 20. Queries are at most 400 characters and
50 words. Check that your plan permits the intended retention before storing
results permanently in a keyword register.

## Datamuse and Wikipedia

```powershell
keywordmoves run datamuse --operation related --keyword "paper planes"
keywordmoves run datamuse --operation synonyms --keyword "flight"
keywordmoves run wikipedia --operation suggestions --keyword "Arcadia" --option language=en
```

Datamuse maps `related` to `/words?ml=`, `synonyms` to `/words?rel_syn=`, and
`suggestions` to `/sug?s=` on `api.datamuse.com`. This adapter uses its English
route and returns the provider's lexical ranking score, not demand evidence.
Public displays should acknowledge Datamuse. Its current documentation announces
API-key requirements from **1 January 2027**; the unauthenticated adapter may
need updating then. No undocumented future key parameter has been guessed.

Wikipedia uses `action=opensearch` on the selected language wiki with namespace
0 and at most 500 titles. It retains page URLs. No user search history or
pageview/search-volume information is queried.

## Direct website access

### Browser autocomplete endpoints: experimental and opt-in

The following query routes are identified in public browser source code, not
sold as stable keyword-research APIs by the search engines:

| Adapter | Actual query route | Response extraction |
| --- | --- | --- |
| `google-autocomplete` | `https://suggestqueries.google.com/complete/search?client=firefox&q={query}&hl=en` | OpenSearch JSON, strings in index 1 |
| `bing-autocomplete` | `https://api.bing.com/osjson.aspx?query={query}` | OpenSearch JSON, strings in index 1 |
| `duckduckgo-autocomplete` | `https://ac.duckduckgo.com/ac/?q={query}&type=list` | OpenSearch JSON, strings in index 1 |

```powershell
keywordmoves run google-autocomplete --operation suggestions `
  --keyword "independent music" --option language=en `
  --option allow_unofficial=true --option limit=10
```

Each run reads the relevant host's `robots.txt` before querying. A disallow,
challenge, redirect, unreadable robots response or excessive crawl delay stops
the query; robots 404/410 means no robots file. There is no bypass switch.
Returned country is unknown rather than inferred from the machine or an ignored
country option. Only the Google route implements a language parameter here.

These routes have synthetic parsing/request tests but **were not live-verified
in the build environment**. They may be blocked or change without notice.
`allow_unofficial=true` acknowledges this status; it does not bypass a block or
establish that a use is permitted. There is no proxy rotation, CAPTCHA solving,
login automation, cookie injection or hidden-endpoint probing.

### Public static HTML: `website-keywords`

For another site, inspect its ordinary public search form and a real public
results page first. Establish a GET query URL, identify the exact repeated
keyword element, and test an explicit no-results marker. Supply those reviewed
rules rather than assuming every site's markup uses the same selector.

This is a **configuration template**, not a working recipe for `example.org`:

```powershell
keywordmoves run website-keywords --operation query `
  --keyword "independent music" `
  --option 'url_template=https://example.org/search?q={query}' `
  --option 'selector=.keyword-result .phrase' `
  --option 'empty_selector=.no-keywords'
```

The plugin percent-encodes the query in the query string. The single `{query}`
placeholder cannot change the hostname or path. Public HTTPS targets only;
private, loopback and link-local resolved addresses, credentials in URLs, custom
ports and redirects are rejected. Robots rules are checked before the result
page. Never place account tokens or other secrets in a public URL template.

This is a local CLI safeguard, **not a hardened multi-tenant URL proxy**. DNS is
checked before requesting but not pinned for the connection, so a service
accepting untrusted URLs also needs network-level egress controls against DNS
rebinding. Do not expose it as an unrestricted hosted fetch endpoint.

Beautiful Soup extracts text from selected DOM elements, skipping script/style/
template content and explicitly hidden elements. This is not a browser's full
computed visibility model. No JavaScript is executed. No selector match is a
layout/access error unless the configured `empty_selector` positively matches.
Login forms and common access challenges are rejected rather than scraped as
keywords. Sites needing POST forms, authentication or dynamic rendering are not
silently treated as successful static sources.

### Saved rendered HTML

For a page you can legitimately view but which needs client-side rendering,
save its rendered result DOM with the necessary result elements, then import:

```powershell
keywordmoves run website-keywords --operation import-html `
  --input .\rendered-results.html `
  --option 'selector=.keyword-result .phrase' `
  --option observed_at=2026-10-02
```

`source_url` and `country` can be recorded. The operation is explicitly marked
`live_query_performed=false`; it is not an automated browser session.

## Ubersuggest and AnswerThePublic: report import, not invented scrapers

Ubersuggest's own support article states that it does not offer API or webhook
functionality. Its public app entry redirects to a JavaScript-based keyword
interface; the inspected public material did not establish an unauthenticated,
stable GET query-to-results contract. AnswerThePublic exposes a public search
form, but this research did not verify a supported developer API or a reproducible
unauthenticated result endpoint. That is **not** a claim that no private or
partner API exists. No internal authenticated routes or fabricated CSS recipes
have been implemented for either tool.

Use their normal interface to run a query and obtain an allowed export, then
import it with explicit column meanings. The example headings below must be
replaced with the exact headings in your own report:

```powershell
keywordmoves run ubersuggest --operation import-csv `
  --input .\keyword-report.csv `
  --option phrase_column=Keyword --option volume_column=Volume `
  --option cpc_column=CPC --option currency=USD `
  --option difficulty_column=Difficulty `
  --option platform=Google --option country=GB `
  --option observed_at=2026-10-02
```

Replace `ubersuggest` with `answerthepublic` for that report. Only
`phrase_column`, `platform` and `observed_at` are required; volume, CPC and
difficulty columns are optional. A CPC column requires its currency. Accepted
delimiters are comma (default), semicolon or `tab`. Files must be UTF-8, with
optional BOM. Numeric values use a dot decimal separator; valid comma thousands
groups are accepted. Ambiguous decimal-comma values and numeric ranges are
rejected, not interpreted as zeros. Missing markers `N/A`, `null`, `-` and empty
cells remain null. A provider-reported zero remains zero.

Both tools also accept `import-html` with the same explicit selector, platform
and report-date requirements. Inputs are marked `access=user-export` and
`live_query_performed=false`. These plugins do not provide live website querying
until a working, reviewable public access contract is established.

## Reusing local candidates

These adapters retain the existing `PluginRequest` / `PluginResult` interface.
For example, take an intentionally bounded list from NLTK, spaCy or KeyBERT and
request one batch of Keywords Everywhere metrics:

```python
from keywordmoves import PluginRegistry, PluginRequest
from keywordmoves.models import ExecutionContext

registry = PluginRegistry()
local = registry.get("nltk").run(
    PluginRequest("extract", options={
        "text": "Paper planes drift over the city at night.",
        "limit": 10,
    }),
    ExecutionContext(llms=None),
)
terms = tuple(dict.fromkeys(candidate.phrase for candidate in local.keywords))
if terms:
    measured = registry.get("keywords-everywhere").run(
        PluginRequest("metrics", keywords=terms[:100],
                      options={"country": "gb", "currency": "gbp", "limit": 100}),
        ExecutionContext(llms=None),
    )
    print(measured.to_dict())
```

The local extractor's optional package/resources must already be installed and
the metrics API key configured. Review the candidates before a paid run. The
example does not silently send the entire library of reference text to a service.
Network discovery sends keyword seeds and selected scope parameters; HTML and
CSV imports process the files locally.

## Tests and implementation limits

```powershell
python -m pip install -e ".[dev,online]"
python -m pytest tests/test_online_api.py tests/test_online_transport.py tests/test_online_websites.py
python -m ruff check .
```

Fixtures under `tests/fixtures/online` are explicitly **synthetic**, not copied
live provider data. Tests use HTTPX MockTransport, exercising real request
encoding, headers and response parsing without credentials, quota use or network
access. They cover every implemented API operation and Keyword Tool platform
route, pagination, null/zero handling, units, credentials, bounds, errors,
robots policies, public URL checks, DOM challenges and imports. This validates
adapter contracts, not subscription entitlements or current live availability.

A separate `online-source-contracts` workflow runs these tests and linting on
Python 3.10–3.13, Linux and Windows. It never spends API credits. Existing
model-backed CI jobs remain separate. No paid-service CI smoke test is enabled.

Provider changes may require an adapter update. This patch does not implement
OAuth authorisation, unlimited page walking, cross-service automatic enrichment,
interactive browsers, account login scraping, account-wide spend limits, every
endpoint of each vendor, or every SEO vendor on the market. The access matrix
above is the exact implemented scope, including its import-only limitations.

## Primary sources and verification trail

All entries below were reviewed on **2026-10-02**. They document the source
contracts or access constraints, not a successful live run with an account.

| Source | Documentation used |
| --- | --- |
| Google Ads | [Keyword ideas](https://developers.google.com/google-ads/api/docs/keyword-planning/generate-keyword-ideas); [historical metrics](https://developers.google.com/google-ads/api/docs/keyword-planning/generate-historical-metrics); [release notes](https://developers.google.com/google-ads/api/docs/release-notes) |
| Search Console | [Search Analytics query contract, date semantics and incompleteness](https://developers.google.com/webmaster-tools/v1/searchanalytics/query) |
| DataForSEO | [Keyword ideas](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_ideas-live/); [suggestions](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_suggestions-live/); [overview](https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_overview-live/) |
| Semrush | [Analytics API keyword reports](https://developer.semrush.com/api/v3/seo/keyword-reports/) |
| Ahrefs | [Keywords Explorer matching terms; CPC units](https://docs.ahrefs.com/en/api/reference/keywords-explorer/get-matching-terms) |
| Keyword Tool | [API v2 routes, per-platform parameters, locations, networks, response fields and partial notices](https://keywordtool.io/api) |
| Keywords Everywhere | [API documentation](https://keywordseverywhere.com/api-documentation.html) |
| AlsoAsked | [Official client README and request/results examples](https://github.com/AlsoAsked/also-asked-php/blob/main/README.md); [API-key header](https://github.com/AlsoAsked/also-asked-php/blob/main/generated/Authentication/ApiKeyAuthentication.php); [request-field normalisation](https://github.com/AlsoAsked/also-asked-php/blob/main/generated/Normalizer/SearchRequestOptionsNormalizer.php) |
| SerpApi | [Google autocomplete](https://serpapi.com/google-autocomplete-api); [related questions](https://serpapi.com/related-questions); [related searches](https://serpapi.com/related-searches) |
| Brave | [Suggestion endpoint](https://api-dashboard.search.brave.com/api-reference/other/suggestions) |
| Datamuse | [Word API, lexical scores, attribution and January 2027 change](https://datamuse.com/api/) |
| Wikipedia | [MediaWiki OpenSearch](https://www.mediawiki.org/wiki/API:Opensearch) |
| Google browser suggestions | [Mozilla search configuration](https://searchfox.org/firefox-main/source/services/settings/dumps/main/search-config-v2.json) |
| Bing browser suggestions | [Mozilla iOS search-data implementation](https://searchfox.org/mozilla-mobile/source/firefox-ios/SampleBrowser/SampleBrowser/Networking/SearchDataProvider.swift) |
| DuckDuckGo browser suggestions | [Mozilla search-plugin XML](https://searchfox.org/comm-central/source/suite/components/search/searchplugins/duckduckgo.xml) |
| Ubersuggest | [Provider's API/webhook statement](https://ubersuggest.zendesk.com/hc/en-us/articles/4405444620059-Ubersuggest-API-and-Webhooks-What-Users-Need-to-Know); [report exports](https://ubersuggest.zendesk.com/hc/en-us/articles/4405444702875-Exporting-Reports-with-Ubersuggest); [app entry inspected](https://app.neilpatel.com/en/ubersuggest/) |
| AnswerThePublic | [Public interface inspected](https://answerthepublic.com/en); [plans and export features](https://answerthepublic.com/en/pricing) |
| Existing Google Trends | [Official API alpha announcement](https://developers.google.com/search/blog/2025/07/trends-api); existing CSV import retained rather than inventing a live contract |

A browser-source endpoint is evidence that a client has used a route; it is not
a supported developer API guarantee or evidence of blanket permission to scrape.
For Ubersuggest and AnswerThePublic the reviewed pages were insufficient to
establish a live query parser. The import-only implementation and this explicit
limitation preserve that uncertainty instead of presenting guessed requests as
working integrations.
