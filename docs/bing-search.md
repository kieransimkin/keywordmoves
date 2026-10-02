# Bing Search discovery and analysis

Research checked: **2 October 2026**. Patch baseline:
`3209f93b98309e7af4173bcde7e9dfd3633c24da`.

The `bing-search` keyword plugin provides **39 read-only operations** for Bing
Webmaster data, market keyword research, suggestions, search-result observations,
provider planning estimates, organic competition and local report comparison.
The existing `bing-autocomplete` and other providers remain unchanged. No
application configuration, search index, sitemap or advertisement is modified.

## API access status matters

Microsoft retired the general Bing Search APIs on **11 August 2025** [1]. This
plugin does not call Azure Bing Search v7, its retired Autosuggest API or a
pretend replacement endpoint. Bing Webmaster Tools is a separate API, with
API-key and OAuth access documented by Microsoft [2,3]. For public search results,
this module uses explicit Bing routes from SerpApi or DataForSEO [21–23]. Those
are third-party services, not Microsoft's first-party Search API.

Grounding with Bing Search supplies web context to generative applications [1];
it is not substituted here for a transparent organic ranking snapshot. The
module does not select an LLM, use your OpenAI key or automatically send reports
to another model. API quotas, scopes, site verification, provider plans and data
licences still apply. Creating/renewing OAuth tokens remains external setup.

**Production verification:** request/response adapters were checked against
primary documentation and synthetic HTTP responses, not live authenticated
accounts. Microsoft’s older Webmaster reference contains inconsistent example
sections on some method pages. The module follows the published method signatures
and the reviewed JSON conventions, but availability and exact wire behaviour
must still be verified with an authorised account before operational use. An
HTTP error is not retried using guessed parameters or another provider.

## Installation

From the updated source checkout:

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

The existing `online` extra supplies HTTPX and Beautiful Soup. JSON/CSV analysis
has no runtime network dependency; HTML import needs Beautiful Soup. Plugin
listing does not import those packages or request credentials. No new package
extra or release-version change is required.

## Credentials and secret handling

| Service | Environment variable(s) | Explicit alternative |
| --- | --- | --- |
| Bing Webmaster API key | `BING_WEBMASTER_API_KEY` | `--option api_key=...` |
| Bing Webmaster OAuth | `BING_WEBMASTER_ACCESS_TOKEN` | `--option auth=oauth --option access_token=...` |
| SerpApi | `SERPAPI_API_KEY` | `--option api_key=...` |
| DataForSEO | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | `--option login=... --option password=...` |
| Keyword Tool | `KEYWORDTOOL_API_KEY` | `--option api_key=...` |

Explicit credentials override the corresponding environment value. A blank
explicit credential fails instead of silently using a different environment
account. `.env` files are not loaded automatically. Prefer your environment’s
secret injection or secure prompt facilities: putting literal keys in commands,
including environment assignments, can leave them in shell history. Keys are
excluded from normal result metadata and provider error messages. External
HTTP debugging, process inspection and application logging are outside this
module's guarantee. The key-based Webmaster API transmits the key as its
documented HTTPS query parameter; avoid logging request URLs [2].

## Bing Webmaster module

Set `BING_WEBMASTER_API_KEY`, then use the actual site URL registered and verified
in your account. The user-level key can access that user's verified properties
[2]; it is not a key for arbitrary competitor-site performance.

```powershell
$env:BING_WEBMASTER_API_KEY = "YOUR_WEBMASTER_API_KEY"
keywordmoves run bing-search --operation bwt-sites
keywordmoves run bing-search `
  --operation bwt-queries `
  --option site_url=https://example.com/ `
  --option limit=100
```

For OAuth, set the token and add `--option auth=oauth`. The adapter uses the
OAuth base documented by Microsoft, `www.bing.com`, with a Bearer header [3].
The key route uses `ssl.bing.com`. Both use `/webmaster/api.svc/json/METHOD`.

### Native operation matrix

These are separate documented operations, not aliases that silently fetch a
different report. Arguments not supported by a method are rejected.

| Operation | Native method | Required input beyond credentials | Output |
| --- | --- | --- | --- |
| `bwt-sites` | `GetUserSites` | None | Accessible URL and verification flag; no verification secrets. |
| `bwt-queries` | `GetQueryStats` | `site_url` | Returned top query rows, clicks, impressions and separate click/impression average positions. |
| `bwt-pages` | `GetPageStats` | `site_url` | Returned page rows using the native `Query` field as the page URL. |
| `bwt-page-queries` | `GetPageQueryStats` | `site_url`, `page_url` | Queries associated with the requested page. |
| `bwt-query-pages` | `GetQueryPageStats` | `site_url`, one `--keyword` | Pages associated with the query. |
| `bwt-query-history` | `GetQueryTrafficStats` | `site_url`, one `--keyword` | Returned dated traffic buckets for a top query. |
| `bwt-query-page-detail` | `GetQueryPageDetailStats` | `site_url`, `page_url`, one `--keyword` | Query/page detail by native position bucket, not an invented average position. |
| `bwt-traffic` | `GetRankAndTrafficStats` | `site_url` | Returned site traffic/date buckets. |
| `bwt-keyword` | `GetKeyword` | One `--keyword`, `country`, `language`, `start_date`, `end_date` | Market keyword and broad keyword impressions for the requested period. |
| `bwt-related` | `GetRelatedKeywords` | Same as `bwt-keyword` | Related phrases and available impression fields. |
| `bwt-keyword-history` | `GetKeywordStats` | One `--keyword`, `country`, `language` | Returned native keyword/date buckets. |
| `bwt-links` | `GetLinkCounts` | `site_url` | Returned link-count records and explicit page continuation. |
| `bwt-url-links` | `GetUrlLinks` | `site_url`, `link` | Returned referring URLs/anchor text for the specified link. |
| `bwt-sitemaps` | `GetFeeds` | `site_url` | Feed URL, status/type, size, URL count, submitted/crawl dates where returned. |
| `bwt-url-info` | `GetUrlInfo` | `site_url`, `url` | URL/domain metadata and available HTTP/crawl/discovery fields. |
| `bwt-crawl` | `GetCrawlStats` | `site_url` | Returned dated crawl, status-code, index/link and error counters. |
| `bwt-crawl-issues` | `GetCrawlIssues` | `site_url` | URL, HTTP code, native issue value and available inlinks. |

See Microsoft references [4–20] for each method. `bwt-url-info` accepts an
absolute URL or `url=domain:example.com`. Its `IsPage` flag distinguishes a page
from a domain record; it is **not** a Boolean statement that a page is indexed.
No URL Inspection, IndexNow submission or live indexing test is implied.

Top query/page methods do **not** take Search Console-style `start_date`,
`end_date`, `device`, `country`, `rowLimit` or `startRow` parameters. They return
native dates and top rows; this module does not fabricate an exhaustive keyword
inventory, retention duration, reporting window or daily series from them.
Microsoft documents weekly updates for several top-report interfaces [4–7].

The JSON adapter preserves WCF `/Date(milliseconds±HHMM)/` offsets, retains native
bucket dates, and treats the .NET minimum-date sentinel as unavailable. Native
`Position` in detailed reports is a separate bucket. Average click and impression
position stay separate. CTR is calculated only with compatible, available clicks
and impressions; zero impressions do not imply a zero CTR.

`query_encoding=documented` is the default. It uses raw `siteUrl`, JSON-quoted
query/page/url/country/language strings, ordinary page indexes and ISO dates.
This follows reviewed Webmaster example conventions [5–8,17]. Because published
examples are inconsistent and some keyword signatures have no JSON example,
`query_encoding=plain` is an explicit alternative for a separately verified
integration. **No automatic encoding fallback or duplicate read is performed.**

Link methods alone support `page_index` (zero-based, default 0, maximum 32767)
and `pages` (default 1, maximum 20). Returned `TotalPages` governs continuation;
`next_page_index` is retained when more pages remain. A local `limit` truncates
diagnostic `records` only after receipt and reports the truncation. No request
is made to an arbitrary backlink destination.

### Native market keyword research

```powershell
keywordmoves run bing-search `
  --operation bwt-keyword `
  --keyword "independent music" `
  --option country=GB `
  --option language=en `
  --option start_date=2026-09-01 `
  --option end_date=2026-09-30
```

`keyword_impressions` and `broad_keyword_impressions` are native research fields
[10–12], not property impressions, organic keyword difficulty, average monthly
volume, or paid-ad competition. Keep country, language and requested dates with
these numbers. `bwt-keyword-history` has no date-filter parameters; it retains
only the buckets actually returned.

### Query opportunities and query/page overlap

```powershell
keywordmoves run bing-search `
  --operation bwt-opportunities `
  --option site_url=https://example.com/ `
  --option min_impressions=100 `
  --option min_position=4 `
  --option max_position=20 `
  --option max_ctr=0.03
```

`bwt-opportunities` filters each returned dimensional row; it does not sum
possibly overlapping native reports. Defaults: minimum impressions 100,
average impression position 4–20 inclusive, maximum CTR 1. An optional
`target_ctr` adds `max(0, target_ctr - observed_ctr) * impressions`. That is
conditional arithmetic **at the same impression count**, not a traffic forecast.

`bwt-overlap` takes `site_url` and one `--keyword` for a live query/page lookup,
or a canonical report through `--input`. It groups pages only within matching
query/date/position-bucket contexts. Multiple visible URLs are not automatically
labelled harmful cannibalisation. Both review operations accept one saved report
instead of API access; no credentials or network calls are needed in that case.

## Bing search results and organic competition

`serp`, `competition`, `rank-check`, `related` and `questions` accept one
`--keyword`. Choose `provider=serpapi` (default) or `provider=dataforseo` explicitly
when selecting your data source. No fallback to Google or a retired Microsoft
endpoint is implemented.

```powershell
$env:SERPAPI_API_KEY = "YOUR_SERPAPI_KEY"
keywordmoves run bing-search `
  --operation competition `
  --keyword "uk garage music" `
  --option provider=serpapi `
  --option market=en-GB `
  --option device=desktop `
  --option location="Brighton, England, United Kingdom" `
  --option target_host=kieransimkin.co.uk `
  --option include_subdomains=true `
  --option top_n=10
```

SerpApi options: required `market` such as `en-GB`; `device=desktop|mobile|tablet`;
optional city `location`; `safe_search=Off|Moderate|Strict` (default Moderate);
optional Bing `filters`; `no_cache=true|false` (default false); `first` (1-based,
default 1); `pages` (default 1). The adapter sends `mkt`, not a conflicting `cc`
parameter [21]. Retrieval time is not a guarantee of fresh indexing; the
provider can return cached results unless its cache is explicitly disabled.

Bing pagination offsets can be irregular. The next returned offset is validated,
then a new request is reconstructed with the original query and credentials.
Returned links are never followed blindly and their keys never replace yours.
Results use organic positions; tracking links and ads are not competitor URLs.

DataForSEO options for these five operations: `location_code` is required;
`language=en` by default; `device=desktop|mobile`; `depth=10..100` (default 10).
This sends one Bing organic live/advanced task. The output distinguishes
`rank_group` (organic rank) from `rank_absolute` (position amongst result features)
[23]. It does not label an ad, local pack or answer block as an ordinary organic
ranking. Missing or unsuccessful task responses fail instead of becoming empty
searches. Provider task ID and reported cost are retained where supplied.

### Competition output

`competition` and `rank-check` report the following without combining them into
a ranking-probability or difficulty score:

- Organic results actually observed within positions 1 through `top_n`.
- Distinct competing hostnames, the largest hostname's share of the observed
  top results, and literal query matches in returned titles.
- Your target hostname's best observed organic position, with optional
  subdomain matching and exact hostname-boundary checks.
- Returned result-feature labels, original titles/snippets and URLs.

`rank-check` requires `target_host`. A missing target returns `null` and
`target_observed=false`, not rank 101, infinity, zero demand or an unindexed
verdict. A page collected from rank 50 is not relabelled as the top 10. Hostnames
are not merged into registrable domains using a guessed public-suffix rule.

`serp` additionally emits returned related searches and questions as candidates.
`related` and `questions` emit only their corresponding suggestion category.
Position in a returned suggestion list is not search volume. Approximate total
result counts remain in snapshot metadata and **never** enter a difficulty
formula. All snapshots have `complete_inventory=false`.

## Autocomplete and provider suggestions

### Documented Keyword Tool route

```powershell
$env:KEYWORDTOOL_API_KEY = "YOUR_KEYWORDTOOL_KEY"
keywordmoves run bing-search `
  --operation suggestions `
  --keyword "uk garage" `
  --option country=GB `
  --option language=en `
  --option suggestion_type=questions
```

`suggestions` uses `provider=keywordtool`. The wrapper forces the **Bing** route,
not the generic adapter's Google default. `suggestion_type` accepts
`suggestions`, `questions`, `prepositions` or `related` [27]. `category` passes an
explicit provider search vertical. Add `metrics=true` plus `location_code` for
volume fields; suggestion geography and metric geography are retained separately.

### Experimental browser autocomplete

```powershell
keywordmoves run bing-search `
  --operation autocomplete `
  --keyword "uk garage" `
  --option market=en-GB `
  --option allow_unofficial=true `
  --option expand=alphabet `
  --option max_queries=2 `
  --option max_requests=4
```

This uses the browser-oriented `https://api.bing.com/osjson.aspx` route, not
Azure Autosuggest. The existing project's Bing adapter uses this same endpoint.
There is no supported API-contract or availability guarantee and no successful
live endpoint verification in this build. It requires opt-in, checks robots policy
for the **full query URL**, validates the query echo and returns only strings
actually supplied by the server. Login redirects, denial and malformed data stop
the run; no challenge bypass or cookie import is implemented.

`expand=none|alphabet|questions|prepositions` includes the original seed followed
by bounded probes. English probe prefixes are deterministic query construction,
not evidence of popular searches. `max_queries` defaults to 1, up to 10;
`probe_offset` resumes the probe list. Each probe reserves two request slots
(robots check and suggestion read). `next_probe_offset` and unqueried probes are
reported. The module does not crawl suggestions recursively.

## Popularity estimates and competitor keywords

```powershell
$env:DATAFORSEO_LOGIN = "YOUR_LOGIN"
$env:DATAFORSEO_PASSWORD = "YOUR_API_PASSWORD"
keywordmoves run bing-search `
  --operation metrics `
  --keyword "paper plane song" `
  --keyword "independent music" `
  --option provider=dataforseo `
  --option location_code=2826 `
  --option language=en `
  --option search_partners=false
```

### DataForSEO routes

| Operation | Route below `/v3/` | Input and interpretation |
| --- | --- | --- |
| `metrics` | `keywords_data/bing/search_volume/live` | Up to 1,000 phrases; location required. `search_volume` is **last month**, not an averaged monthly series [24]. |
| `ideas` | `keywords_data/bing/keywords_for_keywords/live` | Up to 200 seeds; location required. Returns provider-related keywords and available Bing Ads planning metrics [25]. |
| `url-ideas` | `keywords_data/bing/keyword_suggestions_for_url/live` | `target=https://...`, `language`, optional `exclude_brands`; no `--keyword`. Confidence is a keyword/URL match signal, not volume or ranking probability [26]. |
| `competitor-keywords` | `dataforseo_labs/bing/ranked_keywords/live` | `target=hostname`, `location_code=2840`, language; organic results only, provider difficulty/rank and update timestamps when supplied [28]. |

`metrics`/`ideas` retain CPC in USD, paid competition as a provider 0–1 index,
actual year/month volume buckets, language, device when selected, and whether
search partners were included. Monthly null values stay null. An unreturned
input term is reported, not converted to volume zero. `device` may be desktop,
mobile or tablet for planning routes; omission leaves the provider default.
Each phrase must be at most 100 characters. No ad creation or optimisation is
performed. The provider may charge for an entire returned set despite a smaller
local output limit.

The Bing Labs ranked-keyword database currently documents **US-only** support;
the adapter rejects another `location_code` instead of returning Google or
US figures under a UK label [28]. `offset` and `limit` control one database page.
`next_offset` is a continuation hint, not an automatic collection. Database
metrics may have different update times from a fresh Bing result snapshot.
Organic difficulty, if returned, is the provider's 0–100 index, not a probability
that your page will rank. It is never inferred from paid-ad competition or CPC.

### Keyword Tool volume route

`metrics` also accepts `provider=keywordtool`, reusing its existing adapter with
Bing forced. Supply `location_code`, optional `language` (default en), `currency`
(default USD), `metrics_source=keyword_planner|historical_data`, and
`network=ownedandoperatedonly|ownedandoperatedandsyndicatedsearch|syndicatedsearchonly`.
The default network includes owned and syndicated search. Use
`ownedandoperatedonly` to avoid silently including syndicated traffic.

The provider describes its `volume` as average monthly planning volume, unlike
DataForSEO's last-month field [24,27]. The two are not collapsed into one number.
Historical mode can lack CPC/competition. The returned request-level
`device_breakdown`, when available, remains `aggregate_device_breakdown` in
result metadata, **not** a device breakdown for each keyword. Returned trend
and close-variant metadata are also retained without inventing a percentage
interpretation or independent demand for every variant [27].

The inherited Keyword Tool batch limit is deliberately 100 phrases, not the
provider's advertised maximum. `sandbox=true` selects its documented sandbox;
no sandbox call was performed during development.

## Local schemas and analysis operations

Every local route is explicitly offline. At most one input is accepted unless
noted otherwise. Files must be UTF-8 (BOM accepted), and default to a 5 MB cap.
`max_input_bytes` can be set from 1,024 through 20,000,000. Oversized files fail
rather than being truncated. JSON must be finite; invalid numeric units and
duplicate dimensional identities are errors.

| Operation | Input |
| --- | --- |
| `bwt-import` | Canonical Webmaster JSON, wrapped `bing-search` JSON, a reviewed native response or mapped CSV. |
| `bwt-opportunities`, `bwt-overlap` | One canonical/wrapped Webmaster JSON report instead of live reads. |
| `bwt-compare` | Two canonical/wrapped, explicitly scoped **period-total** reports. |
| `import-serp` | One canonical/wrapped Bing search snapshot. |
| `import-serp-html` | Reviewed saved HTML with explicit organic-row selectors. |
| `serp-compare` | Two canonical/wrapped Bing search snapshots. |
| `import-observations` | One canonical/wrapped observation report or explicitly mapped CSV. |
| `compare` | Two canonical/wrapped observation reports. |
| `combine` | 1–20 `bing-search` result JSON files; preserves each original evidence/metadata entry. |
| `keyword-gap` | Two result JSON files; candidates from the first absent from the second. |

`KeywordCandidate.score` stays unset. `sort_by` sorts descending by an available
numeric evidence metric, missing values last; it does not determine that larger
values are always better. `limit=1..50000` (default 100) limits displayed
candidates, but provider-specific request limits can be smaller. The metadata
report/snapshot retains the full bounded received dataset; `output_truncated`
records the candidate display cap. Inspect `records_truncated` for diagnostics.

### Webmaster interchange

Schema: `keywordmoves-bing-webmaster/v1`, `engine: Bing`.
Required top fields: `source`, `observed_at`, `context`, `rows`.
Context requires `site_url`, `report_type`, `scope`, and
`granularity: native-buckets|period`. A period report requires actual ISO
`start_date`/`end_date`; native buckets reject an invented overall period.
Rows retain `query`, `page`, `date`, `position_bucket`, `clicks`, `impressions`,
`ctr`, `average_click_position`, and `average_impression_position` as applicable.

For saved native JSON, add `input_format=bwt-native`, `source`, `observed_at`,
`site_url`, and `report_type` (default queries). A query-specific export requires
its original `query` option, and a page-specific one requires `page_url`. The
parser must not guess missing dimensions from a filename.

For CSV period totals, explicitly declare `period_totals=true` and supply
`site_url`, `source`, `scope`, `observed_at`, `start_date`, `end_date` and column
mappings. Required mappings: `clicks_column`, `impressions_column`, and at least
one of `query_column` / `page_column`. Optional: `ctr_column`,
`impression_position_column`, `click_position_column`. `ctr_is_percent=true`
converts a reviewed percentage column (including `%` suffix); otherwise the
field must already be a fraction. `delimiter` defaults to comma.

`bwt-compare` requires the same source/property/report scope, equal-length,
chronological, non-overlapping periods. Native buckets cannot safely be treated
as period totals and are rejected. CTR differences use percentage points.
A missing row or metric stays unknown, not zero. Lower average position can be
numerically better but the comparison is not a ranking forecast.

### Search-result interchange

Schema: `keywordmoves-bing-serp/v1`, `engine: Bing`.
Required: `source`, `observed_at`, `context.query`, `context.market`,
`context.device`, and `organic: [{rank,url,title,snippet}]`.
Context also retains language, location, safe search, time filter, depth, first
rank and requested page count. Optional arrays: `related`, `questions`,
`features`. `total_results_estimate` is metadata only. Import validates ranks,
deduplicates repeated URLs at their best observed rank, and rejects ambiguous
rank collisions. At most 2,000 organic rows are accepted.

Saved HTML additionally requires `organic_only=true`, `row_selector`,
`link_selector`, original query (`--keyword`), `source`, `observed_at`, `market`
and `device`. Optional `title_selector`, `snippet_selector`,
`no_results_selector`, `first`, `location`, `safe_search`, `filters`. Review
selectors to exclude ads; this module cannot certify an arbitrary selector's
meaning. No rows without a reviewed no-results marker is an error. Export direct
destination URLs rather than Bing click-tracking URLs. No universal live Bing
search-page selector or JavaScript browser driver is claimed.

`serp-compare` requires matching source and complete captured search/collection
context, and compares real instants, not lexicographic timezone strings. It
returns shared-URL overlap and per-URL position changes only where the URL was
observed in both snapshots. Absence is described as not observed, not an exact
lost rank.

### Other dated observations

Schema: `keywordmoves-bing-observations/v1`, `engine: Bing`, `observations` array.
Each entry requires `phrase`, `source`, `scope`, `metric`, `unit`, `observed_at`
and `value` (which may be null). Optional: `geography`, paired `period_start`/
`period_end`, Boolean `approximate`. Known units are validated; explicit other
metrics can be retained without receiving an invented common scale.

CSV mappings: `phrase_column`, `value_column`, optional `metric_column` and
`unit_column`; otherwise specify constant `metric` / `unit`. Source/scope/capture
date are mandatory, and geography/period/approximation apply to the whole file.
Round displays such as `2.5M` must be converted to their reviewed approximate
numeric value and marked `approximate=true`; they are not exact counters.

`compare` only compares matching phrases, source, scope, metric, unit, geography
and measurement-period boundaries. It excludes approximate and missing numbers.
It returns net change, change per elapsed day and percentage change (null for a
zero baseline). This is comparison of repeated measurements of the **same
scope/window**, not a general forecast or automatic comparison of different
months. Use `bwt-compare` for explicit performance-period reports.

## Transport limits and operational controls

Live calls share the existing bounded HTTP client: approved HTTPS hosts,
no redirects, no automatic retries, no secret-bearing raw error bodies, timeouts
and decoded-response size limits. Per-run options:

| Option | Default | Bounds |
| --- | --- | --- |
| `max_requests` | 5 | 1–20, shared by pagination and any composed reads |
| `timeout` | 30 seconds | 1–120 per request |
| `max_response_bytes` | 2,000,000 | 1,024–10,000,000 per decoded response |
| `min_interval` | 1 second | 0–60 requested; cannot undercut adapter minimum |

Adapter minimum intervals are 1 second normally, 2 for browser autocomplete and
4 for Keyword Tool. There is no cross-process scheduler or global-account quota
coordinator; parallel runs must be coordinated externally. HTTP 429 stops the run
rather than sleeping through an unknown provider cooldown and retrying. Set
budgets before running; `limit` and `max_requests` are **not dollar ceilings**.
Calling a paid provider explicitly may consume credits. No paid-capable remote
job or live credentialed request was started during development.

## Coverage boundaries

This module does not claim to cover every Bing/Microsoft advertising product.
Microsoft Advertising's Ad Insight API remains an additional documented avenue
[29], but direct Microsoft Advertising OAuth/customer/developer-token workflows,
campaign search-term reports, conversion attribution and ad management are not
implemented here. Planning access is through Bing-specific providers.

There is no Bing Maps/Places management, Shopping/News/Image-specific adapter,
autonomous Copilot/Grounding collector, personalised AI Performance dashboard
scraper, direct live organic-HTML search crawler, full JavaScript site audit,
IndexNow submission or sitemap submission. Reviewed/licensed observations can be
imported, but an import facility is not a live integration. Generic backlink
analysis elsewhere in KeywordMoves can be useful context but must not be
mislabelled as a Bing-specific authority score. Google Trends is not Bing trend
data and Google keyword difficulty must not be silently substituted.

## Offline demonstration and tests

```powershell
python .\docs\examples\bing-search-workflow.py
```

The example creates nine synthetic reports in a new `bing-search-demo` directory:
performance, opportunities, query/page overlap, period comparison, organic
competition, rank comparison, demand observations, observation comparison and
combined evidence. It makes no requests and refuses to overwrite an existing
output directory. `--output ANOTHER_DIRECTORY` chooses a different destination.
The illustrative fixture data is not real demand or a current search result.

```powershell
python -m pip install -e ".[dev,online]"
python -m pytest tests/test_bing_webmaster.py tests/test_bing_search_providers.py tests/test_bing_search_analysis.py tests/test_bing_search_imports.py
```

Tests exercise actual HTTPX serialization and HTML parsing with synthetic
responses/pages; they do not establish account entitlement or live response
compatibility. Dedicated CI runs these tests, focused Ruff checks, registration
regressions and the example on Linux/Windows and Python 3.10–3.13 without secrets.
Local validation details and unavailable checks are included with the delivered
patch, rather than claiming a remote CI result before the patch is committed.

## Primary references

Accessed 2 October 2026. Microsoft interface signatures define implemented method
names; example responses are illustrative and contain no real measurements.

1. [Microsoft: Bing Search API retirement](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement).
2. [Bing Webmaster API access and API keys](https://learn.microsoft.com/en-us/bingwebmaster/getting-access).
3. [Bing Webmaster OAuth](https://learn.microsoft.com/en-us/bingwebmaster/oauth2).
4. [GetQueryStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getquerystats?view=bing-webmaster-dotnet).
5. [GetPageStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getpagestats?view=bing-webmaster-dotnet).
6. [GetPageQueryStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getpagequerystats?view=bing-webmaster-dotnet).
7. [GetQueryPageStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getquerypagestats?view=bing-webmaster-dotnet).
8. [GetQueryPageDetailStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getquerypagedetailstats?view=bing-webmaster-dotnet).
9. [GetQueryTrafficStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getquerytrafficstats?view=bing-webmaster-dotnet).
10. [GetKeyword](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getkeyword?view=bing-webmaster-dotnet).
11. [GetRelatedKeywords](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getrelatedkeywords?view=bing-webmaster-dotnet).
12. [GetKeywordStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getkeywordstats?view=bing-webmaster-dotnet).
13. [GetUserSites](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getusersites?view=bing-webmaster-dotnet).
14. [GetRankAndTrafficStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getrankandtrafficstats?view=bing-webmaster-dotnet).
15. [GetLinkCounts](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getlinkcounts?view=bing-webmaster-dotnet).
16. [GetUrlLinks](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.geturllinks?view=bing-webmaster-dotnet).
17. [GetUrlInfo](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.geturlinfo?view=bing-webmaster-dotnet).
18. [GetFeeds](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getfeeds?view=bing-webmaster-dotnet).
19. [GetCrawlStats](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getcrawlstats?view=bing-webmaster-dotnet).
20. [GetCrawlIssues](https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getcrawlissues?view=bing-webmaster-dotnet).
21. [SerpApi Bing search parameters and pagination](https://serpapi.com/bing-search-api).
22. [SerpApi Bing related questions](https://serpapi.com/bing-related-questions).
23. [DataForSEO Bing organic live/advanced](https://docs.dataforseo.com/v3/serp/bing/organic/live/advanced/).
24. [DataForSEO Bing search volume](https://docs.dataforseo.com/v3/keywords_data/bing/search_volume/live/).
25. [DataForSEO Bing keyword ideas](https://docs.dataforseo.com/v3/keywords_data/bing/keywords_for_keywords/live/).
26. [DataForSEO Bing URL keyword suggestions](https://docs.dataforseo.com/v3/keywords_data/bing/keyword_suggestions_for_url/live/).
27. [Keyword Tool API: Bing networks, metrics sources, suggestions and device breakdown](https://keywordtool.io/api).
28. [DataForSEO Bing Labs ranked keywords and US-only availability](https://docs.dataforseo.com/v3/dataforseo_labs/bing/ranked_keywords/live/).
29. [Microsoft Advertising GetKeywordIdeas](https://learn.microsoft.com/en-us/advertising/ad-insight-service/getkeywordideas?view=bingads-13).
