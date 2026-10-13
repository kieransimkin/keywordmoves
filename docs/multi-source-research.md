# Multi-source research

Since 0.5.5, [automatic free collection](automatic-free-collection.md) removes
manual HTML imports for the YT public-form JSON and supported browser routes.
`research-batch` accepts free `ytkeywordtool/collect-public`,
`vidiq/collect-public`, and both `collect-browser` source operations. Browser
tasks require an authorised context driver. Paid Keywords Everywhere `metrics`
remains excluded; access failures are retained as unavailable tasks.

Reviewed YT Keyword Tool suggestions and free Keywords Everywhere Google planning
context compose offline through `import-public-html`. The batch contract rejects
live Keywords Everywhere metrics requests. See [free public-source modules](free-public-source-modules.md)
for capture instructions, metric scope and sources held after follow-up checks.

The free public vidIQ overview can be composed with `vidiq/import-public-html`.
It preserves US rounded monthly displays separately from seven-day growth with
unstated geography. See [free-source checks](free-youtube-volume-sources.md).

Keyword Tool YouTube JSON can be composed offline with `keywordtool/import-json`.
Live Keyword Tool routes are excluded from batches because they may be metered.
See [YouTube demand sources](youtube-demand-sources.md) for required scope, dated
monthly history, 12-month average estimates and free Trends export imports.

Research date: 11 October 2026. KeywordMoves retains source-native evidence rather
than averaging unrelated scores. More sources improve coverage only when their
scope, access rights, freshness and audience intent are reviewed.

## Access and useful evidence

| Source | Route | Useful evidence and limits |
| --- | --- | --- |
| Google Search Console | `google-search`, `search-console`, authorised exports | Property queries, clicks and impressions; privacy-filtered, not market volume. |
| Google Ads | `google-ads` / Google workbench | Regional planning estimates; authorised OAuth/customer/developer access. |
| Google Trends | Supported exports, `google-trends` | Normalised interest and related queries; threshold zero is not no searches. Native API access is restricted. |
| Bing Webmaster | `bing-search` | Account keyword/history/related reports; access and measurement window matter. |
| YouTube | `youtube` | Studio referrals, authorised Analytics, observed discovery, permitted reference text; channel counts are not platform search totals. |
| TikTok / Instagram / Reddit | Named workbenches | Permitted native observations, exports and eligible APIs; content counts are not search volume. |
| Pinterest / Facebook / X / LinkedIn / Yahoo / DuckDuckGo | `native-export`, `observed-evidence`, supported engine routes | Reviewed native displays and exports with explicit metrics and scope. No hidden-endpoint scraping. |
| Ahrefs / Semrush / DataForSEO / Keyword Tool / Keywords Everywhere | Existing dedicated adapters | Regional third-party volume/competition estimates; account, tariff and exact-request approval required. Public free tool displays can use reviewed imports. |
| AlsoAsked / SerpApi / Brave | Existing dedicated adapters | Questions and suggestions; access/credits/retention rights require review. |
| Ubersuggest / AnswerThePublic | Existing reviewed imports | Exported metrics and wording; no live-query implementation is claimed. |
| Datamuse | `datamuse` | Semantic/synonym wording, music topic and context hints, English/Spanish vocabulary; lexical scores are not demand. |
| Wikipedia OpenSearch | `wikipedia` | Topic/title vocabulary; not popularity. |
| Wikimedia Pageviews | `wikimedia-pageviews` | Human-classified article-reading attention over a complete declared daily window; not keyword searches, song demand or geography. |
| Apple iTunes Search | `itunes-catalog` | Song-title, credited artist and genre context for a storefront; no popularity, affiliation or audience inference. |
| SE Ranking | `seranking` | Documented regional estimates, questions/related/similar terms and language-only long tails; explicit paid credit guard. |
| vidIQ | `vidiq` | Authorised saved JSON/native reviewed keyword captures; offline imports retain censoring, worldwide/country estimates and provider dates. No live scraper or independently reproduced estimate model. |
| Last.fm / MusicBrainz / ListenBrainz / Spotify / Apple Music | Evaluate official API or permitted exports before integration | Potential music tags, catalogue identity and listening context. Credentials, eligibility, commercial-use terms, rates and licensing are separate gates; no new live integration is claimed here. |

This list is a routing inventory, not a claim that every source has been accessed
live or provides equivalent keyword data. The new free adapters were checked
against live responses; SE Ranking has synthetic contract coverage only.

## Explicit research batches

~~~powershell
keywordmoves run research-batch --operation validate-plan --input examples/research-plan.json
keywordmoves run research-batch --operation collect --input examples/research-plan.json
~~~

`validate-plan` is offline. Plans have schema `keywordmoves-research-batch/v1`
and 1–200 explicit tasks. Each task requires unique `id`, `subject`, `plugin`,
`operation`, string-list `keywords`/`inputs` and flat scalar `options`. Paths are
resolved from the caller's working directory. Credentials are prohibited in plans:
use the environment or the existing opt-in OS credential store.

Supported network sources are Datamuse, Wikipedia, Wikimedia Pageviews, Apple
iTunes and SE Ranking. Supported import sources are native-export,
observed-evidence, Google Trends, Ubersuggest and AnswerThePublic. Other adapters
remain available through their dedicated commands: the batch runner does not
pretend to enforce tariffs it cannot calculate. Browser-only sources require
dated reviewed captures/exports, not automation through this runner.

Every result retains task identity, subject, original source result and units.
Offline `vidiq`, `candidate-focus` and `candidate-shortlist/select` tasks also compose here; read their
[import and phrase-review contract](vidiq-benchmark.md).
Known source failures become unavailable tasks with their error class, without
retaining arbitrary upstream error bodies. Empty results remain no-candidates;
they do not establish zero demand. There is no automatic seed expansion, retry,
pagination or paid fallback. Each network task defaults to one request. Existing
adapter options may allow a different explicitly reviewed request budget.

A metered task requires a caller-supplied approval record with `confirmed=true`,
`reference`, positive `finite_charge_ceiling` and `charge_unit=credits`. This
records human approval; it does not manufacture it. For SE Ranking the ceiling
must equal `options.approved_max_credits`, with `paid_requests_approved=true`.
Actual authorisation, current tariff verification and an account spending cap
remain the caller's responsibility. The local guard is not provider-enforced.

## Added source contracts

### SE Ranking

Set `SERANKING_API_KEY` in your selected credential route. Select the provider's
database (`source=uk`, not GB). One request only, offset zero:

~~~powershell
# Run only after exact source/seed/scope/charge approval.
keywordmoves run seranking --operation related --keyword "example music" --option source=uk --option limit=10 --option paid_requests_approved=true --option approved_max_credits=100
~~~

The documented tariff on the research date is 100 credits for `metrics`, 10 per
returned term for `related`/`similar`/`questions`, and 1 per returned `longtail`
string. The guard conservatively bounds the requested limit. `metrics` accepts
up to 1,000 explicit seeds in this adapter. Longtail strings have no volume or
difficulty. Other operations retain estimated monthly searches, paid competition
on a 0–1 scale and organic difficulty on the provider's 0–100 scale separately.
Missing data is null; currency is not guessed from a database code.

### Wikimedia Pageviews

~~~powershell
keywordmoves run wikimedia-pageviews --operation article --keyword "Shipping Forecast" --option project=en.wikipedia.org --option start_date=2026-09-12 --option end_date=2026-10-09
~~~

Windows are 1–366 completed UTC days, starting on/after 1 July 2015. Returned
scope, duplicate days and invalid values are checked. AQS identifies a project
as `en.wikipedia` in responses and `en.wikipedia.org` in request URLs; both
represent the same project. Missing days stay unknown, with completeness and
daily series retained. Wikipedia language is not an audience geography filter.

### Apple iTunes catalogue

~~~powershell
keywordmoves run itunes-catalog --operation songs --keyword "example" --option country=GB --option attribute=songTerm --option limit=20
~~~

`attribute` is songTerm or artistTerm. The source returns title/artist/genre,
track identity and store links, with no demand metrics. No audio previews or
artwork are retained. Apple documents approximately 20 requests/minute; callers
must coordinate across processes and cache rather than assuming a per-request
client enforces a shared rate. Storefront availability is not UK listening demand.

### Datamuse context

`topics` accepts up to five words; `left_context` and `right_context` are one-word
hints. `vocabulary` is en or es; unsupported vocabularies fail rather than silently
claiming multilingual coverage. Persian text requires an appropriate source.

~~~powershell
keywordmoves run datamuse --operation related --keyword friendship --option topics=music,song,lyrics --option limit=20
~~~

Datamuse's current official documentation gives conflicting January/February
2027 dates for API-key requirements. Recheck access before use then. Attribute
Datamuse if displaying its results publicly.

## Target review

Count distinct phrases per subject, not duplicate platform rows. Separate branded
retrieval from new-listener discovery. Require truthful content fit, listening or
story intent, an appropriate surface and an explicit experiment/review metric.
Keep exact-term evidence distinct from a broader topic's evidence. High demand
for another artist, medical advice, a repair tutorial or an unrelated news event
does not make it a suitable song target. Unmeasured but useful terms remain test
proposals; do not label them validated low-competition opportunities.

## Potential problems

- An import task rejects `max_requests`: imports have no network budget. The batch
  runner injects its default only for network tasks; contract tests cover this.
- Wikimedia rejects otherwise correct project scope: check request domains versus
  AQS project identifiers. The adapter now accepts the documented equivalent forms
  while still rejecting a different project/agent/article/window.
- A provider fails or returns no candidates: retain the unavailable/empty task,
  inspect that source through its dedicated route, and continue unaffected tasks.
  Do not retry a metered source or switch providers without reviewing approval.
- UI volume is `<100` or `>1000`: retain the display as a censored estimate in
  native-export/observed-evidence. Do not turn it into an exact number.

## Primary sources

- [SE Ranking API](https://seranking.com/api/data/keyword-research/)
- [Wikimedia Analytics API](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html)
- [Apple iTunes Search API](https://performance-partners.apple.com/search-api)
- [Datamuse API](https://www.datamuse.com/api/)
- [Google Trends data](https://support.google.com/trends/answer/4365533)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [Last.fm API](https://www.last.fm/api)
- [ListenBrainz API](https://listenbrainz.readthedocs.io/en/latest/users/api/)
- [Spotify Web API](https://developer.spotify.com/documentation/web-api)

For comparative claims, benchmark identical seeds, market, period, cost, source
access and human-reviewed relevance. Broad source support alone does not prove
superiority to vidIQ or any other product.
