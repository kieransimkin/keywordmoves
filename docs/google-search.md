# Google Search keyword discovery and analysis

Research date: **2 October 2026**. Upstream baseline:
`666630ed5ffe9a8fdaadaf6d922d14e32404cad2`.

The `google-search` plugin is a Google Search workbench, not a replacement for
`youtube`. It contains an expanded read-only Search Console module, explicit
provider routes for discovery/demand, organic-result analysis, technical page
context, and source-compatible offline comparisons. The original
`search-console`, `google-ads`, `google-trends` and commercial plugins keep their
existing interfaces. Generative LLMs are not selected or invoked.

**Access and verification:** request contracts were researched from the primary
sources at the end of this guide. Tests use real HTTPX serialization with
synthetic responses, not live accounts or billable requests. Browser autocomplete
has not been verified against a successful current live response. Account
permissions, available data, current provider entitlements and production
response shapes still require an authorised smoke test.

## The three questions that must stay separate

**Demand:** how often people search, or how their interest changes. Google Ads
planning estimates, provider volume estimates and Google Trends relative indices
answer different versions of this question. Close variants, language, geography,
network and historical window matter. Search Console impressions describe the
visibility of the authorised property, not all searches in the market. [1–3,6–7]

**Organic competition:** which URLs occupy the sampled organic positions, how
many distinct hostnames appear, how their titles match the phrase, which features
share the result page, and what provider-specific difficulty/backlink evidence
says. These are review inputs, not a guaranteed ranking outcome. A count of
results returned for a broad query—or an `allintitle:` count—is not converted into
a validated difficulty formula. This implementation deliberately has no universal
0–100 score or “easy keyword” verdict.

**Paid competition:** advertiser competition and CPC/bid ranges describe an
advertising auction, not the effort needed to rank a page organically. They are
never substituted for organic difficulty. Native provider units remain attached:
Google Ads bids in account-currency micros, Ahrefs CPC in US cents, and other
providers' explicit currency/units. [6–7,15–18]

## Installation

From the repository root, after applying the follow-on patch:

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

Core installation and plugin listing remain dependency-free. HTML parsing uses
Beautiful Soup only when needed; online operations import HTTPX lazily. This
module adds no new optional dependency group.

The default output is the usual `PluginResult` JSON. `KeywordCandidate.score`
remains `null`; evidence has separate source, metric, unit, observation date and
geography. Provider response metadata is deliberately selected rather than
copying request headers/credentials into results.
Combined outputs retain each original candidate's dimensional metadata and its
associated evidence, plus report context such as dates, filters, location and
device. Equal counts from different pages are not silently treated as the same
underlying observation; the top-level evidence list is a union, not a sum.

## Operation matrix: 35 implemented operations

| Operation | Source or input | Purpose |
| --- | --- | --- |
| `gsc-query` | Search Console API or saved canonical report | Dimensional query performance; configurable grouping and filters. |
| `gsc-pages` | Search Console API or saved report | Page grouping by default. |
| `gsc-query-pages` | Search Console API or saved report | Query/page grouping by default. |
| `gsc-opportunities` | Search Console API or saved report | Filter observed rows for review; optional explicit CTR scenario. |
| `gsc-overlap` | Search Console API or saved report | Multiple observed pages for the same query/context. |
| `gsc-sites` | Search Console API | Accessible property inventory and permission levels. |
| `gsc-sitemaps` | Search Console API | Submitted sitemap status; no submission or deletion. |
| `gsc-inspect` | URL Inspection API | Indexed-version status of supplied URLs in the property. |
| `gsc-import` | Canonical/native JSON or mapped CSV | Validate and normalise an existing report. |
| `gsc-compare` | Two canonical reports/results | Comparable equal-length period changes. |
| `gsc-bulk-sql` | Explicit BigQuery table/property/dates | Generate parameterised bulk-export aggregation SQL; **does not execute it**. |
| `serp` | SerpApi or DataForSEO | Organic results, related queries/PAA, features and returned AI references. |
| `competition` | SerpApi or DataForSEO | Descriptive organic-competition evidence for one query. |
| `rank-check` | SerpApi or DataForSEO | The supplied hostname's best position in the retrieved organic sample. |
| `autocomplete` | SerpApi, or opted-in browser endpoint | Suggestions with optional bounded alphabet/question/preposition probes. |
| `questions` | SerpApi/DataForSEO, AlsoAsked, Ahrefs or Keyword Tool | PAA or provider question candidates; no silent recursive expansion. |
| `suggestions` | DataForSEO, Keyword Tool, Semrush or Ahrefs | Provider-native keyword expansion. |
| `ideas` | Google Ads, DataForSEO or Semrush | Seed-based ideas. |
| `metrics` | Google Ads, DataForSEO, Keyword Tool, Semrush or Keywords Everywhere | Native volume, paid competition, bids/CPC and available organic difficulty. |
| `ads-url-ideas` | Google Ads | URL, site or combined keyword/URL seed ideas. |
| `competitor-keywords` | DataForSEO Labs | Provider-observed organic ranking keywords for an explicit target. |
| `backlinks` | DataForSEO Backlinks | Explicit page/domain backlink summary. |
| `trends` | SerpApi Google Trends | Web Search time series, top/rising queries/topics and regional indices. |
| `custom-search` | Legacy Google Custom Search JSON API | Existing-customer configured-engine results; not a google.com rank proxy. |
| `pagespeed` | Google PageSpeed Insights | Lighthouse lab measurements for an explicit URL. |
| `page-audit` | Public URL or supplied HTML | Literal title/H1/body coverage and metadata for specified terms. |
| `import-serp` | Canonical JSON or previous result | Re-analyse a saved organic-results snapshot. |
| `import-serp-html` | Saved HTML and reviewed selectors | Explicit organic-result extraction; no universal Google CSS selector assumed. |
| `serp-compare` | Two compatible snapshots | URL overlap and observed position changes. |
| `import-observations` | Canonical JSON or mapped CSV | Source-labelled metrics from other reviewed tools/exports. |
| `observed-compare` | Two observation snapshots/results | Compatible exact metric deltas; rounded displays excluded. |
| `trends-import` | Google Trends interest-over-time CSV | Confirmed Web Search relative indices, preserving `<1` censoring. |
| `combine` | 1–10 KeywordMoves result JSON files | Deduplicated keyword union, retaining separate evidence and origins. |
| `keyword-gap` | Own-list result, then comparison-list result | Terms absent from the supplied own list, **not proof of no ranking**. |

## Shared controls, credentials and preservation

`--keyword` supplies a seed, exact term or URL depending on the operation.
Single-query operations reject multiple seeds instead of quietly using one.
`--input` is repeated for comparisons. File paths for imports must be readable
UTF-8 (a BOM is accepted). Inputs are bounded by `max_response_bytes`; JSON must
contain finite numbers. Output keys and JSON schemas use ordinary strings and
numbers, with unavailable values represented by `null`.

| Setting | Default / boundary |
| --- | --- |
| `limit` | 100 output candidates; 1–50,000 globally, often 1–1,000 on provider routes. |
| `max_requests` | 5 calls; explicitly configurable 1–20 per run. |
| `timeout` | 30 seconds; 1–120. |
| `max_response_bytes` | 2,000,000 decoded bytes per response/file; 1,024–10,000,000. |
| `min_interval` | Cannot reduce the provider's minimum interval. Keyword Tool uses at least four seconds. |
| `sort_by` | An emitted evidence metric, or `phrase`. Does not synthesize a score. |
| `sort_order` | `desc`; choose `asc` for ranks/positions where smaller is better. Requires `sort_by`. |

An output limit is **not a universal spending cap**. Some providers charge per
request, keyword, task, returned row or endpoint. There are no automatic retries,
provider fallback, recurring tasks or unbounded pagination. Composed routes reuse
the same per-run HTTP budget. A Google SERP request through DataForSEO can crawl
several requested pages inside one billed task. Review provider costs before
running authenticated commands. No paid request was made during development.

| Account | Environment variable(s) | Explicit equivalent |
| --- | --- | --- |
| Search Console | `SEARCH_CONSOLE_ACCESS_TOKEN` | `access_token` |
| SerpApi | `SERPAPI_API_KEY` | `api_key` |
| DataForSEO | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | `login`, `password` |
| Google Ads | `GOOGLE_ADS_ACCESS_TOKEN`, `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_API_VERSION` | `access_token`, `developer_token`, `api_version` |
| Keyword Tool | `KEYWORDTOOL_API_KEY` | `api_key` |
| Semrush | `SEMRUSH_API_KEY` | `api_key` |
| Ahrefs | `AHREFS_API_KEY` | `api_key` |
| Keywords Everywhere | `KEYWORDS_EVERYWHERE_API_KEY` | `api_key` |
| AlsoAsked | `ALSOASKED_API_KEY` | `api_key` |
| Legacy Custom Search | `GOOGLE_CUSTOM_SEARCH_API_KEY` | `api_key` |
| Optional PageSpeed key | `PAGESPEED_API_KEY` | `api_key` |

An explicit invalid/blank credential is an error, not permission to fall back to
another environment key. Prefer secret injection/environment variables to CLI
secrets; literal commands may enter history and command-line values may be
visible to other processes. `.env` files are not loaded automatically. OAuth
consent, refresh tokens, access-token renewal and provider account setup are
external setup steps. No property/ad/index mutation is implemented.

### Geography is intentionally not one interchangeable code

Search Console filters use three-letter codes such as `gbr` and device values
`MOBILE`, `DESKTOP`, `TABLET`. SerpApi uses its Google-country code (`uk` in the UK
examples) and lower-case devices. Keyword Tool suggestions use `GB`; its Google
volume route requires a numeric location ID such as `2826`. DataForSEO also
requires `location_code`, while Semrush uses `database=uk`. Do not assume that
identically spelled option names imply identical provider populations. [1,8,10,15]

## Search Console: setup and query workflows

Enable the Search Console API in an approved Google Cloud project. Obtain an
OAuth access token whose principal can access the property. Prefer the read-only
`https://www.googleapis.com/auth/webmasters.readonly` scope. Supply the exact
property name: `sc-domain:example.com` or an exact URL-prefix ending in `/`. An
API key alone is not sufficient for private property data. [1,4–5]

```powershell
$env:SEARCH_CONSOLE_ACCESS_TOKEN = "YOUR_OAUTH_ACCESS_TOKEN"
keywordmoves run google-search `
  --operation gsc-query `
  --option site_url=sc-domain:example.com `
  --option start_date=2026-09-01 `
  --option end_date=2026-09-30 `
  --option "dimensions=query,country,device" `
  --option country=gbr `
  --option device=MOBILE `
  --option page_size=25000 `
  --option pages=2 `
  --option max_rows=50000 `
  --option limit=1000
```

Report dates are inclusive and in Pacific Time. `observed_at` is when data was
retrieved/imported, not the reporting period. The complete set of rows retained
by this invocation is stored in `metadata.gsc_report`, even when `limit` truncates
the displayed keyword list. `retained_rows`, `next_start_row` and
`pagination_may_have_more` describe collection; none imply a complete query
inventory. There can be additional row limits and anonymised/unreturned queries,
especially with page/query grouping. Google documents a 25,000-row response limit
and at most 50,000 rows/day/search type; pagination does not remove all data-loss
or privacy limitations. [1–2]

Filters and grouping:

- `dimensions`: CSV/list of `query,page,country,device,date,hour,searchAppearance`;
  empty dimensions request property-level totals. `gsc-pages` defaults to page,
  `gsc-query-pages`/`gsc-overlap` to query,page, others to query.
- `search_type`: `web` (default), `image`, `video`, `news`, `discover`, `googleNews`.
  These are distinct surfaces, not one merged organic-demand measurement.
- `data_state`: `final` (default), `all`, or `hourly_all`. `hour` grouping and
  `hourly_all` must be selected together. Returned first-incomplete date/hour
  metadata is retained. Hourly rows must be aggregated before period comparison.
- `aggregation`: `auto` (default), `byPage`, `byProperty`, `byNewsShowcasePanel`,
  with documented incompatible combinations rejected. Query searchAppearance
  alone first, then use `appearance` to filter the detailed query. [1–2]
- One `--keyword` becomes a query filter. `query_operator` supports `contains`
  (default), `equals`, `notEquals`, `notContains`, `includingRegex`,
  `excludingRegex`. Google interprets RE2 patterns; query/page `equals` is case
  sensitive. `page`, `country`, `device` and `appearance` add exact filters.
- `filters_json`: additional filter objects, with `dimension`, `operator`,
  `expression`. The combined filters are ANDed. At most 20 are allowed locally;
  expressions are limited to 4,096 characters. No unsupported OR group is sent.

A branded/non-branded split can be made with two explicit runs using an approved
brand regex and `includingRegex`/`excludingRegex`; no user preference or brand
matching is inferred automatically.

### Opportunities and query/page overlap

```powershell
keywordmoves run google-search `
  --operation gsc-opportunities `
  --input .\saved-gsc-result.json `
  --option min_impressions=100 `
  --option min_position=4 `
  --option max_position=20 `
  --option max_ctr=0.02 `
  --option target_ctr=0.04
```

The default filters are impressions >=100 and observed average position 4–20.
`max_ctr` defaults to 1 (no restrictive CTR assumption). `target_ctr` is optional:
when supplied, `max(0,(target_ctr-observed_ctr)*impressions)` is explicitly labelled
**hypothetical additional clicks at the same impressions**, not predicted traffic.
There is no universal expected CTR curve or claim that position alone determines
click-through rate. This operation can instead fetch a report live using the
same date/property options as `gsc-query`.

`gsc-overlap` requires query/page grouping. It keeps all remaining dimensions
separate: the same query in two countries is not accidentally merged. It returns
pages, available clicks/impressions/position and each page's share of returned
page impressions. Multiple pages are a review signal, not proof of keyword
cannibalisation. Summed page impressions are not unique property impressions.

`gsc-compare --input before.json --input after.json` accepts canonical reports or
saved plugin results. Periods must be chronological, non-overlapping and equal
length; property, dimensions, type, filters, data state, source scope and actual
aggregation must match. CTR deltas use percentage points; lower position is
better. Missing rows remain unknown, never zero. This is deliberately stricter
than an arbitrary comparison of unrelated CSV exports.

### Inventory, inspections and bulk export

`gsc-sites` lists accessible properties without changing them. `gsc-sitemaps`
requires `site_url` and returns available errors/warnings and download/submission
status. `gsc-inspect` takes up to ten URL seeds, verifies they belong to the
supplied property, and calls the indexed-version URL Inspection endpoint. It is
not a live URL test, an indexing submission or a ranking guarantee. [4–5]

```powershell
keywordmoves run google-search `
  --operation gsc-bulk-sql `
  --option table=my-project.searchconsole.searchdata_url_impression `
  --option site_url=sc-domain:example.com `
  --option start_date=2026-09-01 `
  --option end_date=2026-09-30 `
  --option "dimensions=query,page,country,device"
```

This generates SQL plus named parameters/types in metadata. **It does not create
exports, call BigQuery or run a billable job.** Execute a reviewed query in your
approved environment and import its aggregated CSV/JSON. Table names are
restricted to `project.dataset.searchdata_site_impression` or
`project.dataset.searchdata_url_impression`. Position uses the correct native
sum field for each table and adds one after impression-weighted division.
CTR is `SUM(clicks)/SUM(impressions)`, not an unweighted average of row CTRs.
Raw bulk rows can repeat dimensions and must be aggregated; anonymised query
rows are excluded. Dates bound the partition scan. Filters/data_state from the
Search Analytics API are rejected for this SQL generator rather than silently
ignored. Extend the reviewed SQL explicitly for other dimensions/filters. [3]

### Search Console import contracts

`gsc-import` accepts `.json`/`.csv`. Canonical JSON contains schema
`keywordmoves-google-search-console/v1`, property, report dates, capture date,
source scope, dimensions and dimensional rows; see the synthetic fixtures.
Saved `google-search` JSON with `metadata.gsc_report` is also accepted without
restating date options. Raw API JSON and CSV require `site_url`, `start_date`,
`end_date`, `observed_at`, `source`, `scope` and the correct `dimensions`; specify
`aggregation` so comparisons reflect actual collection.

CSV recognises `Top queries`/`Query`, `Top pages`/`Page`, `Clicks`, `Impressions`,
`CTR`, `Position`, and common date/country/device headings. Each can be overridden
with `<field>_column=Heading`. Only explicitly marked percentages are divided by
100: `3%` becomes 0.03, while a raw `3` in CTR is invalid. Grouped numbers such as
`1,000` must be correctly quoted CSV. No locale-dependent decimal-comma guessing
is performed. Duplicate dimensional rows and malformed records fail explicitly.

## Search-result competition and rank checking

```powershell
$env:SERPAPI_API_KEY = "YOUR_API_KEY"
keywordmoves run google-search `
  --operation competition `
  --keyword "paper plane song" `
  --option provider=serpapi `
  --option country=uk `
  --option language=en `
  --option device=desktop `
  --option top_n=10 `
  --option target_host=example.com
```

`serp` additionally returns the related-query/PAA candidates. `rank-check`
requires `target_host`. SerpApi needs `country`, accepts an explicit provider
`location` string and pages 1–5; pages are requested through a fixed API endpoint,
not arbitrary returned URLs. DataForSEO uses `provider=dataforseo`, numeric
`location_code`, language and desktop/mobile; its task's crawl-page count is
bounded. The canonical snapshot carries query, location, language, country,
device, source, capture timestamp, scope and the returned organic rows. [8,11]

The analysis reports the number of observed organic results, distinct hostnames
in the requested top N, the largest hostname's share of those rows, literal
query-phrase title matches and the target's best observed organic position. Ads
are excluded. Hostnames only normalise a leading `www.`; no public-suffix list is
used to pretend different subdomains are one registrable domain. Query strings
and paths remain significant when deduplicating URLs.

`features_returned` identifies available ads, local packs, knowledge/answer
panels, videos/news, PAA, related queries and AI overview features. When providers
return AI overview references, their source links are retained separately from
organic ranks. A follow-up-required flag does not trigger another paid request.
Missing feature fields are not proof that a feature could not appear. Search
results are contextual samples and different providers can disagree. Google
states that traffic from its AI search features is included in Search Console's
Web reporting; this module does not invent a separate GSC AI-only query filter.
[8–9,20]

`serp-compare` checks identical query/source/engine/scope/geography/device and
comparable timestamps. It computes URL overlap in the same top-N window and
position improvements only for URLs observed in both. A disappearing URL is
“only_before”, not “deindexed” or assigned an invented position 101.

### Organic difficulty and backlink context

Use `metrics provider=dataforseo` for a supplied phrase's provider difficulty
alongside its demand and paid-competition fields. Ahrefs candidate discovery
also carries its native difficulty field. These remain separate provider indices,
not directly comparable calibrated probabilities. The legacy Semrush adapter
returns its existing keyword reports and **does not claim to provide organic
difficulty** merely because it returns an advertising-competition column.
[12,15–18]

`backlinks --option target=https://competitor.example/specific-page` retrieves one
DataForSEO summary. A page summary is not a domain summary: always examine the
explicit target. It preserves backlinks, referring domains, referring main
domains, referring pages/IPs, broken backlinks and the provider's native rank
when available. That rank is not Google's PageRank. `include_subdomains` defaults
true and `backlink_status` to live (also lost/all). No automatic paid enrichment
of every SERP URL occurs. [13]

`competitor-keywords --option target=example.com --option location_code=2826`
uses DataForSEO Labs with `item_types=[organic]`, not its mixed paid/organic
selection. `limit` and `offset` bound the provider database query. Keyword,
observed organic rank, ranking URL, estimated volume and available difficulty are
retained. Provider update timestamps are not replaced by the fetch timestamp.
Use exported results with `keyword-gap` to find **supplied-list differences**;
absence in a bounded or stale list is not evidence a domain never ranks. [14]

## Autocomplete, People Also Ask and related searches

```powershell
keywordmoves run google-search `
  --operation autocomplete `
  --keyword "uk garage" `
  --option provider=serpapi `
  --option country=uk `
  --option language=en `
  --option expand=alphabet `
  --option max_queries=5
```

`expand=none` is the default. `alphabet` uses the seed and A–Z suffixes;
`questions` uses the seed plus how/what/why/where/when/which/can prefixes;
`prepositions` uses for/with/without/near/versus/like/to suffixes. Only returned
suggestions become evidence. Generated probes are not represented as popular
queries. `max_queries` is 1–10 (default 5); `expansion_offset` resumes a bounded
slice, and `next_expansion_offset`/`unqueried_probes` explain remaining work. [9]

`questions` defaults to SerpApi PAA from a result page. Individual results retain
available `next_page_token`. Sending that token in a **new explicit** questions
request makes one related-question continuation call; there is no recursive tree
walk or hidden task creation. `provider=dataforseo`, `alsoasked`, `ahrefs` or
`keywordtool` select their documented question route. Those routes are not
interchangeable measurements of demand. [8–10,18–19]

For an experimental direct browser route, set `provider=web` and
`allow_unofficial=true` with `autocomplete`. It uses the same Firefox-client
Google suggestion endpoint already identified by the existing browser adapter,
checks robots policy, verifies the returned query echo, and fails on redirects,
blocking, HTML/challenges or schema changes. Country targeting is **not verified**
for that browser route and is not claimed in its evidence. Two requests per
probe are budgeted (robots and suggestions). This is not a stable developer API,
not a login-cookie collector, and not a bypass for restricted website access.
Use the documented provider route when available, or explicit saved observations.

## Planning estimates and keyword expansion

Provider selection is explicit; no fallback spends another account's credits.

| Provider | Supported workbench routes | Required targeting / notes |
| --- | --- | --- |
| `google-ads` | ideas, metrics | customer_id, location_codes, language_id (default 1000), explicit api_version or environment. Google Search only. |
| `dataforseo` | ideas, suggestions, metrics | numeric location_code, language; offset for list routes. |
| `keywordtool` | suggestions, questions, metrics | country for suggestions; numeric location_code for Google metrics; language, currency, network. |
| `semrush` | ideas (related), suggestions (broad-match), metrics | database; single seed. |
| `ahrefs` | suggestions, questions | country, match_mode=terms/phrase; exact metrics route is not implemented in this adapter. |
| `keywords-everywhere` | metrics | country, currency; batch term list. |
| `alsoasked` | questions | country, language; optional depth/fresh/sandbox. |

The workbench forces Keyword Tool's platform to Google and defaults its network
to `googlesearch`. Explicit `network=googlesearchnetwork` requests the broader
provider network and stays labelled. `suggestion_type` supports ordinary
suggestions, questions, prepositions and related terms. `metrics=true` adds the
provider's available estimates and may change billing; with Google metrics it
requires a numeric location ID even when suggestions already specify a country.
[15]

```powershell
$env:DATAFORSEO_LOGIN = "YOUR_LOGIN"
$env:DATAFORSEO_PASSWORD = "YOUR_API_PASSWORD"
keywordmoves run google-search `
  --operation metrics `
  --keyword "paper plane song" `
  --keyword "independent music" `
  --option provider=dataforseo `
  --option location_code=2826 `
  --option language=en
```

`ads-url-ideas` supports `seed_type=url`, `site` or `keyword-url`. Supply `url`,
customer_id, location_codes, optional language_id and an explicit Google Ads
version, plus approved OAuth/developer credentials. Only `keyword-url` accepts
keyword seeds as well as the URL. A returned page token is exposed; it is not
followed implicitly. No campaigns, budgets or bidding configurations are changed.
Keyword planning can aggregate close variants and native monthly volumes are
retained for review. [6–7]

## Trends, technical review and legacy search

`trends` uses SerpApi's Google Trends API, default `data_type=TIMESERIES`.
`RELATED_QUERIES`, `RELATED_TOPICS` and `GEO_MAP_0` are also implemented. Supply a
country/region and optional timeframe (default `today 12-m`), category and
language. Web Search is the property used. The returned native timeline retains
censored displays and partial flags; the plugin does not replace a `<1` display
with an exact zero. Top indices, rising percentage displays and Breakout labels
remain different quantities. `trends-import` handles interest-over-time CSV;
explicitly confirm `search_property=web` because that property may not be encoded
in the file. It preserves exact zero separately from censored values. Google's
first-party Trends API remains an access-controlled alpha; this patch does not
guess that private contract or claim the SerpApi route is Google's API. [21–22]

`pagespeed --option url=https://example.com/page --option strategy=mobile`
requests Lighthouse lab context: performance score, available LCP/FCP/TBT/CLS,
units, fetch time and Lighthouse version. These are not keyword-difficulty scores
or guarantees of ranking. This patch does not add a separate CrUX field-data
client. [23]

`page-audit` accepts either a public `url` plus keyword seeds, or one saved HTML
file plus `observed_at`. It reports title/H1/body literal matches, visible word
count in static HTML, meta description, canonical link and meta robots. It does
not prescribe keyword density or infer that Google indexed the page. Public
fetches require HTTPS, a public resolved destination, robots permission and
bounded responses; redirects are refused. No linked pages are fetched. Protect
network egress in shared deployments: DNS validation is not a substitute for a
sandbox/firewall against DNS rebinding. Render JavaScript externally and import
a reviewed saved page when appropriate.

`custom-search` is an explicitly legacy route: Google has closed the Custom
Search JSON API to new customers and schedules the existing-customer service to
end on **1 January 2027**. The operation requires `existing_customer=true`, an
existing key, `cx` and country; this flag grants no access. Results belong to the
configured Programmable Search Engine, not an unbiased google.com rank sample.
No Vertex AI Search integration is claimed here. [24]

## Offline evidence, schemas and workflow composition

`import-serp` accepts schema `keywordmoves-google-serp/v1` or a previous result
with `metadata.serp_report`. It requires query/source/scope/capture date and a
bounded organic array with explicit positive ranks and absolute destination URLs.
Locale/device fields should be populated accurately before comparing snapshots.
The checked-in fixtures document the whole shape.

`import-serp-html` requires a saved page plus `organic_selector`, `title_selector`
and `link_selector`, a query seed, `source`, `scope`, `observed_at`, `country` and
optional language/device/location. Each selected organic block must have one
title and direct destination link. Optional `rank_attribute` supplies explicit
page ranks; otherwise positions are order within that saved selection, not an
inferred position on an unseen earlier page. `snippet_selector` is optional.
Review exclusion of advertising/promoted results yourself. Selectors are retained
and must match between comparisons. No login, challenge or empty selector match
is interpreted as zero competition.

`import-observations` supports schema `keywordmoves-google-observations/v1` or
CSV columns `phrase,metric,value,unit,source,scope,observed_at`, with explicit
`<field>_column` overrides. Optional context: country, language, device, window,
approximate. Values are numeric or null. Mark rounded/censored provider displays
as approximate instead of implying precision; attach native units. `observed-compare`
only compares matching phrase/metric/unit/source/scope/geography/device/window
identities. Capture dates must be chronological. Missing or approximate values
do not acquire deltas; zero denominators do not produce infinite percentage
changes. Custom indices may be compared only on their own unchanged definition.

`combine` merges candidate spellings case-insensitively, keeps each source's
evidence and the originating metadata, and never averages conflicting volume or
difficulty values. `keyword-gap` takes the own list first and the comparison list
second. Neither operation silently calls APIs, reparses a competitor's website
or uses an LLM. Existing spaCy/NLTK/KeyBERT/local-text candidates can be included
as candidates, but their text salience is not promoted to demand evidence.

### Offline demonstration

```powershell
python .\docs\examples\google-search-workflow.py
```

This creates a new `google-search-demo` folder containing Search Console
normalisation, opportunity/overlap review, period comparison, SERP competition,
rank comparison, imported demand/difficulty and a combined evidence result. All
measurements and domains are **synthetic**; no network requests occur. Use
`--output NEW_DIRECTORY` for another destination; existing directories are not
overwritten. The example is also a Python API recipe for UTF-8 result persistence.

Windows PowerShell 5.1 redirection can produce UTF-16. Imports here expect UTF-8;
use the Python example's `Path.write_text(..., encoding="utf-8")` approach for
reliable saved JSON rather than assuming every shell redirection has the same
encoding.

## Other avenues and explicit limits

This is a researched, defined workbench, not an implementation of every endpoint
or every possible search surface. The following boundaries prevent misleading
coverage claims:

- **Search Console UI-only reports:** the exposed API family is Search Analytics,
  sites, sitemaps and URL Inspection. A separate Links-report export can be
  reviewed/imported; it is not available through an invented GSC Links API.
  Automatic OAuth, property verification, bulk-export setup, indexing submission
  and BigQuery execution are not included. [5]
- **AI Mode/AI Overview:** returned provider references and feature presence are
  retained, but no dedicated conversational AI Mode session or recursive AI
  follow-up is started. There is no unsupported AI-only GSC dimension. [20]
- **Maps/Business Profile, Merchant/Shopping, Images, News, Discover:** native
  Search Console types/appearance filters and general SERP feature presence are
  supported; dedicated local-map grid tracking, Merchant Center query reports,
  Business Profile Performance, product feeds and image/Lens discovery clients
  are not implemented. Do not compare local-pack positions to organic ranks.
- **Ads search-term/conversion reports and forecasts:** this module uses planning
  data, not a GAQL campaign-performance or conversion attribution client. It does
  not infer organic demand from advertising spend. GA4 conversions and landing
  page engagement can be imported as separately scoped evidence; they do not
  recover hidden organic query text automatically.
- **Other commercial interfaces:** the existing online plugins/importers provide
  additional sources, including Ubersuggest and AnswerThePublic exports. No
  guessed login-only endpoints, CAPTCHA bypass, proxy rotation or private-account
  scraper is added. Search result HTML extraction is explicit and saved-input
  based; the module does not claim a universal current Google SERP scraper.
- **Competition/quality:** no automatic E-E-A-T verdict, universal authority
  threshold, link-buying recommendation, allintitle-derived KGR score, CTR/rank
  forecast, ranking guarantee, semantic intent classifier or new full-site crawl.
  Review content/intent, actual top URLs and comparable evidence separately.
- **Persistence:** results are explicit files chosen by the caller. There is no
  scheduler/database sync, raw credential storage or silently repeated paid
  check. Comply with each source's data usage/retention conditions.

## Tests and failure handling

Install `.[dev,online]`. New tests require no credentials, models or external
requests:

```powershell
python -m pytest tests/test_google_search_analysis.py tests/test_google_search_console.py tests/test_google_search_imports.py tests/test_google_search_providers.py
```

A dedicated CI job covers Python 3.10–3.13 on Linux and Windows, focused linting
and the offline example. Missing keys, wrong field types, invalid locale/filter
combinations, contradictory schemas, duplicate rows/ranks, HTTP errors and
unexpected HTML produce clean configuration/input/source errors. Network errors
and provider rejection messages are not echoed with credentials. Invalid
permissions or blocked pages are failures, not zero-demand observations.

The development verification report shipped with the patch distinguishes
executed local tests from skipped optional model/SDK tests. No live authenticated
requests, paid runs, Search Console property changes, ads changes or index changes
were performed. Ruff and build tools were not available locally; CI is the next
place to run those checks, not evidence that they have already passed.

## Primary references

Accessed 2 October 2026. Links are sources for contracts and constraints, not a
claim that each linked product/endpoint has a live smoke-tested integration.

1. Search Analytics request/response, filters and hourly metadata:
   https://developers.google.com/webmaster-tools/v1/searchanalytics/query
2. Search Console extraction limits, aggregation and search appearance:
   https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data
3. Search Console bulk tables, repeated keys and sum-position fields:
   https://support.google.com/webmasters/answer/12917991?hl=en
   https://support.google.com/webmasters/answer/12917174?hl=en
4. Indexed-version URL Inspection:
   https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect
5. Search Console exposed API resources and quota:
   https://developers.google.com/webmaster-tools/v1/api_reference_index
   https://developers.google.com/webmaster-tools/limits
6. Google Ads keyword ideas and URL seeds:
   https://developers.google.com/google-ads/api/docs/keyword-planning/generate-keyword-ideas
7. Google Ads historical planning metrics:
   https://developers.google.com/google-ads/api/docs/keyword-planning/generate-historical-metrics
8. SerpApi Google Search, organic results and related questions:
   https://serpapi.com/search-api
   https://serpapi.com/organic-results
   https://serpapi.com/related-questions
9. SerpApi Google autocomplete:
   https://serpapi.com/google-autocomplete-api
10. SerpApi explicit related-question expansion:
    https://serpapi.com/google-related-questions-api
11. DataForSEO live advanced Google SERP contract and example:
    https://dataforseo.com/help-center/how-to-scrape-google-search-results-with-python-using-dataforseo-serp-api
12. DataForSEO Labs keyword overview:
    https://docs.dataforseo.com/v3/dataforseo_labs-google-keyword_overview-live/
13. DataForSEO backlink summaries:
    https://docs.dataforseo.com/v3/backlinks-summary-live/
14. DataForSEO Labs organic ranked keywords:
    https://docs.dataforseo.com/v3/dataforseo_labs-google-ranked_keywords-live/
15. Keyword Tool supported platforms/metrics:
    https://keywordtool.io/api
16. Semrush keyword reports:
    https://developer.semrush.com/api/v3/seo/keyword-reports/
17. Keywords Everywhere API:
    https://keywordseverywhere.com/api-documentation.html
18. Ahrefs matching terms and native units:
    https://docs.ahrefs.com/en/api/reference/keywords-explorer/get-matching-terms
19. AlsoAsked supported search contract:
    https://developers.alsoasked.com/
20. Google's AI Search features and Search Console reporting:
    https://developers.google.com/search/docs/appearance/ai-features
21. SerpApi Trends API:
    https://serpapi.com/google-trends-api
22. Google's first-party Trends API alpha:
    https://developers.google.com/search/apis/trends
23. PageSpeed Insights:
    https://developers.google.com/speed/docs/insights/v5/get-started
24. Custom Search JSON API access and retirement:
    https://developers.google.com/custom-search/v1/overview

Existing generic adapters and their research are documented separately in
[online-sources.md](online-sources.md); local keyword proposal methods are in the
spaCy, NLTK and KeyBERT guides.

## Related Trends CSV exports

Use **trends-related-import** with country, observed_at, seed_keyword, scope, window, category and search_property to import a reviewed Top/Rising CSV through the shared Google Trends parser. Top indices, Rising percentages and Breakout labels remain distinct; both sections retain the same phrase when present. There is no network request or normalised demand score. See [monitoring](monitoring.md) for comparison context and [native-export](native-export.md) for other reviewed files.
