# YouTube keyword, hashtag and audience-language research

Research checked **2 October 2026**. Patch baseline:
`4ffe7667f29b65e76d8f4319da4a2f65aa162052`.

The independent `youtube` keyword plugin has **29 operations**. It uses official
YouTube APIs where their contracts fit, documented third-party providers for
additional discovery, and explicit imports for browser/account-only evidence.
It neither posts nor modifies channel/video metadata. The only remote write is
an explicitly approved, potentially billable Apify collection job.

This is a map of the major practical routes, not a claim that every YouTube
surface, analytics vendor or authenticated website has a working integration.
Network adapters were tested with HTTPX synthetic responses, not live accounts.
The two browser endpoints are experimental and were not live-verified.

## Install and list

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

There are no new dependencies beyond the existing `online` extra (HTTPX and
Beautiful Soup). Local text/JSON/CSV/subtitle analysis does not need those
libraries; HTML parsing needs Beautiful Soup. Listing plugins imports none of
the optional packages and reads no keys. No generative LLM is selected.

## Evidence: what "popular" can mean

A hashtag is literal `#text` in a title/description/comment. A **video tag** is
an entry in `snippet.tags`; it is kept as `kind=video-tag`, not silently changed
into a hashtag. A plain search query is another separate category. Unicode
hashtag forms and original source spans are retained; spaced phrases are not
converted into invented hashtags.

There is no dedicated hashtag resource or global hashtag-volume measurement in
the reviewed Data API methods. `hashtag` searches with `q=#tag`, fetches the
returned videos' complete snippets, and retains only exact literal matches in
their titles/descriptions. It reports how many search hits failed that check.
Zero matches mean **not observed in this bounded sample**, not nonexistent,
banned, zero-demand, or shadow-banned. YouTube's `search.pageInfo.totalResults`
is approximate/capped and never becomes a hashtag count or query volume [1,2].

The implementation distinguishes these evidence types:

| Evidence | Interpretation |
| --- | --- |
| `reported_video_count`, `reported_channel_count` | An explicitly sourced display/report; record its date, scope, country, definition and rounding. Not generated from a search sample. |
| `estimated_search_volume` | A named provider's estimate, not a YouTube-published platform total. |
| `suggestion_position` / `returned_order` | Order within one suggestion response, not volume or difficulty. |
| `sample_record_count`, `occurrences` | Frequency in the collected material, not population size. |
| Raw `supporting_records[].counters` | Available whole-video/comment measurements, with nulls and approximation flags. |
| `sample_views_*`, etc. | Optional statistics over an explicit bounded sample. Missing/rounded values are excluded from exact aggregates and counted separately. |
| `attributed_views` | The authorised channel's Analytics views referred by a specific search term or hashtag during the selected interval. Not all searches for that phrase. |
| Google Trends index | Relative interest in the selected comparison, never a count of searches. |

`KeywordCandidate.score` stays unset. Sort using an identified metric rather
than a manufactured combined score. Supported `sort_by` values are `provider`
(default), `alphabetical`, `occurrences`, `reported_video_count`,
`reported_channel_count`, `estimated_search_volume`, `sample_views_median`,
`sample_views_sum`, and `attributed_views`. Unknown values fail before requests.
Unavailable sort measurements go last; contradictory measurements are not
resolved by choosing whichever is most flattering.

### Sample analysis

When derived statistics are enabled, each candidate retains exact available
sum/mean/median values for views, likes, comments, shares and dislikes, and the
number of records actually supplying each measurement. Shares/dislikes are not
assumed publicly available. The like-plus-comment/view ratio uses only records
with all three exact measurements and positive views; it reports its denominator.
This is engagement on the whole video, not engagement caused by the keyword.

Co-occurring hashtags retain shared-record counts and optional Jaccard ratios.
There are source dates, distinct known-channel counts, and 7/30-day publication
counts when timestamps are supplied. Relative strings such as "two days ago"
are preserved by the relevant provider adapter as displays, not fabricated
publication timestamps. Shorts/live labels require explicit source flags; a
short duration or `#shorts` alone does not classify a Short. Current Shorts
view counting and older series can differ [3].

IDs deduplicate records across pages. Conflicting duplicates are reported, with
the first retained, rather than summed or silently taking maximum counters.
Title/description occurrences carry source-field character offsets; video tags
carry list indices. Subtitle offsets refer to cleaned cue text. Phrases do not
cross punctuation, hashtag spans, stopwords, lines, fields or separate records.
The built-in stopword list is English; hashtags accept Unicode and other
languages can use the existing spaCy/NLTK/KeyBERT plugins for richer analysis.

### API-derived statistics and retention

Public API routes default to raw counters plus literal-text indexing; custom
numeric averages, sums and ratios are disabled. Google currently makes expanded
API-derived metrics conditional on acceptance of an additional amendment [4].
After completing the applicable process for your API client, enable them with:

```powershell
--option derive_metrics=true --option api_derived_metrics_accepted=true
```

The flag records your declaration; it does not accept a contract, grant API
access, or establish permission. For local/provider samples `derive_metrics`
defaults to true. Imports marked `api_data=true`, or native `youtube#...` records,
receive the API defaults. Do not relabel API data to bypass its obligations.

Results identify a conservative API refresh/delete date. The plugin does not
silently delete files or implement a retention scheduler. Manage saved outputs,
backups, logs and access revocation yourself under the applicable terms. The
additional policy allows certain accepted statistical uses for up to 36 months,
while text such as descriptions and comments remains subject to the 30-day
refresh/deletion requirements; authorised Analytics data has its own continuing
authorisation requirements [4,5]. This module is not a compliance certification.

## Operation reference

### Official YouTube Data API

| Operation | Inputs | Behaviour |
| --- | --- | --- |
| `search` | One `--keyword` | Searches videos by default, or channel/playlist snippets with `search_type=channel`/`playlist`. Videos are hydrated in batches of up to 50. |
| `hashtag` | One literal `--keyword` | Search, hydrate, verify exact title/description hashtag, discover co-occurring hashtags and caption/title words. |
| `videos` | Repeated video IDs/canonical HTTPS URLs, at most 200 | Fetch snippets, tags, statistics and available details. Missing/unavailable IDs are reported. |
| `channel` | `channel_id`, `handle`, or `mine=true` | Retrieve one channel's metadata/statistics and bounded uploads via its uploads playlist, not an approximate channel search. |
| `playlist` | `playlist_id` | Traverse playlist entries and hydrate distinct accessible videos. Private/deleted entries do not turn into zeros. |
| `popular` | Optional `country`, `category_id` | Current `mostPopular` chart, explicitly not a whole-site trend census. |
| `comments` | `video_id` or one `--keyword` video ID | Top-level comment language, with separately paginated replies when `include_replies=true`. |
| `captions-list` | `video_id`, owner/editor OAuth | Available caption track metadata, not transcript text. |
| `captions` | `video_id`, `caption_id`, owner/editor OAuth | Confirm track association, download VTT, then analyse cue text. |

Authentication for public reads:

```powershell
$env:YOUTUBE_API_KEY = "YOUR_RESTRICTED_DATA_API_KEY"
keywordmoves run youtube --operation hashtag --keyword "ukgarage" `
  --option country=GB --option language=en --option pages=2 --option limit=50
```

Explicit option alternative: `--option api_key=...`. Prefer secure environment
injection rather than literal secrets in shell history/process arguments.
Enable the Data API in your Google Cloud project and restrict the key appropriately.
Google's API Console and documentation are authoritative for actual quota [1,6].
The plugin records `endpoint_calls` and `request_count`, not a potentially stale
universal quota-cost formula. Every page/details/reply request consumes resources;
failed requests may also consume quota. There is no credential/key rotation.

For OAuth instead, set `YOUTUBE_ACCESS_TOKEN` (or `access_token`) and use
`auth=oauth`. Token creation, consent, refresh and app approval are external.

```powershell
keywordmoves run youtube --operation channel --option handle=@YourChannel
keywordmoves run youtube --operation playlist --option playlist_id=YOUR_PLAYLIST_ID
keywordmoves run youtube --operation videos --keyword YOUR_VIDEO_ID
keywordmoves run youtube --operation comments --option video_id=YOUR_VIDEO_ID `
  --option include_replies=true --option pages=1 --option reply_pages=1
```

`YOUTUBE_CHANNEL_ID` supplies a fallback UC-prefixed channel ID for channel and
Analytics operations. `channel_id`, `handle`, and `mine` are mutually exclusive.
For a bounded identity/profile check without fetching uploads, use:

```powershell
keywordmoves run youtube --operation channel --option mine=true `
  --option include_uploads=false --option max_requests=1
```

`include_uploads=false` returns the one accessible channel's metadata in
`metadata.channel` after exactly one `channels.list` request. It works with
`mine=true` (owner OAuth), `handle` or `channel_id`, needs no uploads playlist,
and returns no video-derived keyword candidates or demand estimates. Missing
or hidden counters stay missing; they are not converted to zero. The option is
validated before network access and is rejected for other operations. Omit it
for the existing bounded upload-analysis behaviour. The collection mode is
retained in the evidence scope and metadata. An offline reproducible example is
[`examples/youtube-channel-profile.py`](examples/youtube-channel-profile.py).

Channel titles/descriptions, rounded/hidden subscriber counters and topic details
are retained in metadata; channel/profile keyword research can also use
`search_type=channel`. Upload analysis does not assign channel subscriber totals
to each video keyword [7,8].

Search options: `order=relevance|date|viewCount|rating|title`, `country`,
`language`, `channel_id`, `published_after`, `published_before`,
`duration=any|short|medium|long`, `caption=any|closedCaption|none`,
`event_type=completed|live|upcoming`, `definition=any|high|standard`,
`category_id`, and `safe_search=none|moderate|strict`. Video-only filters are
rejected for channel/playlist searches. Language biases relevance; it does not
guarantee every result's language. Region filters refer to the query's country
context/viewability, **not the geography of lifetime video counters** [1].

Pagination uses only opaque next-page tokens, never arbitrary next URLs or
`totalResults`. `page_size` defaults to 50 (maximum 50; comments can use 100),
`pages` defaults to 1 (maximum 10), and `reply_pages` defaults to 1 (maximum 5).
The per-run HTTP budget applies across search, hydration and replies, so a large
reply request can stop at the budget instead of finishing silently. Commenter
names/avatars/profile IDs are not included in output; user-supplied comment text
can itself contain personal information and still needs careful handling [9,10].

The current `mostPopular` behaviour covers Music, Movies and Gaming chart
content rather than the former general Trending page [3]. `duration=short` is
not the same as Shorts. Since 31 March 2025 Shorts view counters include starts
and replays without the former minimum watch-time requirement [3].

Captions require appropriate owner/editor authorisation (normally the documented
`youtube.force-ssl` scope). Public watchability is not permission to download a
caption track through the API. Tracks may list yet still be unavailable to the
calling token. There is no private timedtext/Innertube workaround [11,12].

### Authorised Analytics API

These routes require a channel-owner OAuth token with both
`https://www.googleapis.com/auth/youtube.readonly` and
`https://www.googleapis.com/auth/yt-analytics.readonly`. The current
`reports.query` contract requires the additional YouTube read scope; the
Analytics read scope alone is insufficient. Monetary access is not requested.
Set `YOUTUBE_ACCESS_TOKEN` and `YOUTUBE_CHANNEL_ID`, and provide
`start_date` / `end_date` as ISO dates [13,14,29]. See
[authenticated access](authenticated-access.md) for external consent, secure
credential injection and bounded verification. A Studio or vidIQ sign-in does
not create a token for this local client.

| Operation | Native report |
| --- | --- |
| `analytics-search` | `YT_SEARCH` traffic-source details: search phrases that referred views. |
| `analytics-hashtags` | `HASHTAGS` traffic-source details: actual hashtag-page referrals. |
| `analytics-traffic` | Traffic-source categories, including search, hashtags, suggested videos, Shorts, etc., as returned by YouTube. |
| `analytics-videos` | Native per-video report: views, engaged views, watch minutes, average duration/percentage, likes, comments, shares and subscriber gains/losses. |

```powershell
$env:YOUTUBE_ACCESS_TOKEN = "YOUR_CHANNEL_OWNER_OAUTH_TOKEN"
$env:YOUTUBE_CHANNEL_ID = "YOUR_UC_CHANNEL_ID"
keywordmoves run youtube --operation analytics-hashtags `
  --option start_date=2026-09-01 --option end_date=2026-09-30 `
  --option sort_by=attributed_views
```

Change to `analytics-search` for actual incoming search phrases. `country` and a
single `video_id` optionally restrict the reports. Detailed reports request
`views,engagedViews,estimatedMinutesWatched`, sorted by views; `report_limit` is
at most **25** because this is the documented report constraint. It is not
silently paginated as an unlimited report. The other reports allow a local
`report_limit` up to 200. A full limit is marked; privacy/report thresholds can
suppress rows. No absent phrase is assumed to have zero traffic [13,14].

`analytics-videos` places measurements and units under
`metadata.video_reports`, **not as candidate phrases equal to video IDs**.
It does not allocate a video's performance to every tag. Keyword-level native
attribution is available specifically from the search/hashtag referral reports.
Analytics HASHTAGS includes VOD hashtag pages and Shorts hashtag pivot pages;
SHORTS-feed views are a different traffic source [14].

### Provider APIs and experimental web routes

| Operation | Provider | Authentication and scope |
| --- | --- | --- |
| `suggestions` | Keyword Tool YouTube | `KEYWORDTOOL_API_KEY`; `country` required; default `suggestion_type=hashtags`, alternatives `suggestions`, `questions`, `prepositions`. |
| `metrics` | Keyword Tool YouTube | Repeated phrases/hashtags; provider search estimates, not public video counts. |
| `serp-search` | SerpApi | `SERPAPI_API_KEY` or `serpapi_key`; native YouTube video/Shorts results and related searches; bounded pagination. |
| `dataforseo-search` | DataForSEO | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`; explicit `location_code`; YouTube organic live/advanced search. |
| `autocomplete` | Historical browser endpoint | No key; explicit `allow_unofficial=true`; robots checked; unstable and not live-verified. |
| `public-hashtag` | Public YouTube hashtag page | No key; explicit opt-in; exact matching experimental header extraction; not live-verified. |

```powershell
$env:KEYWORDTOOL_API_KEY = "YOUR_API_KEY"
keywordmoves run youtube --operation suggestions --keyword "uk garage" `
  --option country=GB --option language=en --option suggestion_type=hashtags `
  --option metrics=true --option sort_by=estimated_search_volume --option limit=50
```

Keyword Tool supports a **YouTube-specific hashtag suggestion type**. These are
provider-suggested hashtags, not independently verified literal use. `metrics`
also accepts ordinary queries; their scope remains distinct. CPC/competition
come from Google Ads proxy data in the documented contract and are named
`google_ads_cpc_proxy` / `google_ads_competition_proxy`; neither is organic
YouTube ranking difficulty [15]. Explicit key options are
`keywordtool_api_key` (preferred wrapper option) or `api_key`.

```powershell
$env:SERPAPI_API_KEY = "YOUR_API_KEY"
keywordmoves run youtube --operation serp-search --keyword "#ukgarage" `
  --option country=gb --option language=en --option pages=2

$env:DATAFORSEO_LOGIN = "YOUR_LOGIN"
$env:DATAFORSEO_PASSWORD = "YOUR_PASSWORD"
keywordmoves run youtube --operation dataforseo-search --keyword "uk garage" `
  --option location_code=2826 --option language=en --option block_depth=20
```

SerpApi's video, Shorts and related-query blocks are handled; ad rows are excluded
and search positions are preserved as source-relative positions, not universal
personalised rankings. For a rounded display such as "2M views", the sample
retains the approximation even when the provider expands it to an integer.
DataForSEO uses native `block_depth` (not a made-up `depth` parameter), checks
both task and envelope status, and handles only organic `youtube_video` records;
other result types are counted as excluded. Actual provider schemas and API
plans determine availability/cost. A local output limit is not a billing cap
and paid read requests are not automatically retried [16,17,18].

The browser autocomplete adapter calls the historical browser suggestion route
with `client=firefox&ds=yt`. `public-hashtag` reads the canonical HTTPS hashtag
page and recognises the historical `ytInitialData.hashtagHeaderRenderer` shape,
checking the requested name and explicit English labelled counts. It does not
execute scripts. Compact K/M/B counts are approximate. Missing/challenged
markup raises an error, not a zero; there is no fallback to hidden APIs.
Robots permission alone does not grant permission to scrape. These routes need
an operator's permission review and successful live smoke test before reliance.
Saved-HTML imports are the reproducible alternative. The module does not claim
that SerpApi's Google autocomplete API is a YouTube autocomplete API [19].

### Apify collection: submit, then fetch

`apify-start` supports **eight presets** against the reviewed Streamers actors:

| `actor` option | Seed arguments | Dataset |
| --- | --- | --- |
| `search-videos` | Search phrases | videos |
| `hashtag-videos` | Literal hashtags | videos |
| `channel-videos` | UC IDs or @handles | videos |
| `playlist-videos` | Playlist IDs | videos |
| `video-details` | Video IDs/canonical HTTPS URLs | videos |
| `shorts` | Search phrases, Shorts-only result limit | videos |
| `streams` | Search phrases, stream-only result limit | videos |
| `comments` | Video IDs/canonical HTTPS URLs | comments |

These are third-party scraper routes, **not official YouTube API endpoints**.
They require permission to collect/process the data and a provider account.
`results_per_seed` defaults to 20, at most 500, with at most 20 seeds. Video
collection disables actor subtitle/KVS and AI-description/summary features.
The comments actor accepts `comment_order=NEWEST_FIRST|TOP_COMMENTS`. Its native
`cid`, `comment`, `voteCount`, `videoId`, `replyToCid` fields are recognised;
repeated parent-video titles are not counted as comment language [20,21].

```powershell
$env:APIFY_TOKEN = "YOUR_API_TOKEN"
keywordmoves run youtube --operation apify-start --keyword "ukgarage" `
  --option actor=hashtag-videos --option results_per_seed=30 `
  --option allow_paid=true --option max_charge_usd=1
```

**This starts a paid-capable remote job.** `allow_paid=true` and an explicit
finite `max_charge_usd` are mandatory. The request sends the provider's
`maxTotalChargeUsd` and a timeout (`actor_timeout`, default 120 seconds).
The provider controls billing/enforcement; this is not a universal guarantee
against all account costs. No automatic retries, polls or restarts occur [22].

The response reports `run_id` and `scope`. After completion, fetch that same run,
preserving the returned scope or an equally explicit description of collection
settings:

```powershell
keywordmoves run youtube --operation apify-fetch --option run_id=RETURNED_ID `
  --option dataset_kind=videos --option scope=RETURNED_SCOPE `
  --option page_size=100 --option pages=2 --option sort_by=sample_views_median
```

Only `SUCCEEDED` runs are read. Fetch checks dataset size, reads bounded pages,
uses the collection's `finishedAt` as its capture time, and retains fetch time,
run/actor/build/dataset IDs and reported provider usage. Dataset kinds are
`videos`, `comments`, or canonical `observations`. The caller must select the
correct YouTube dataset; other-platform flags and invalid video IDs are rejected.
Rows received do not imply all possible matching content was collected.

## Local analysis, exports and browser evidence

| Operation | Accepted content |
| --- | --- |
| `extract` | Inline `text` and repeated UTF-8 reference files; literal candidate discovery only. |
| `import-videos` | JSON/CSV video records, including native Data API items and supported scraper fields. |
| `import-comments` | JSON/CSV comments with stable IDs, text, parent/video IDs and available likes. |
| `import-observations` | Long-form metric observations from reviewed exports/tools. |
| `import-html` | Saved rendered HTML with explicit CSS selectors; no browser login or JavaScript automation. |
| `import-transcript` | UTF-8 `.txt`, `.srt`, `.vtt` supplied by the user; cue boundaries preserved. |
| `compare` | Two saved `keywordmoves-youtube-result/v1` outputs. |
| `trends-import` | Existing Google Trends interest/related CSV, with explicit YouTube Search-property confirmation. |

Imports require `source`, `scope`, `observed_at` (ISO date or timezone-aware
timestamp). Do not use file modification time or today's date for old captures.
`api_data=true` must be supplied when importing non-native-shaped API data.
Inputs are UTF-8 with optional BOM; wrong schema, malformed CSV, oversize input
and invalid counts fail rather than silently truncating or turning into zeros.

Canonical video example (synthetic):

```json
[
  {
    "id": "AAAAAaaaa01", "platform": "YouTube",
    "title": "Paper planes #UKGarage", "description": "#Brighton music",
    "tags": ["independent music", "garage"],
    "viewCount": "1000", "likeCount": 50, "commentCount": 0,
    "published_at": "2026-10-01T12:00:00Z", "content_type": "video"
  }
]
```

```powershell
keywordmoves run youtube --operation import-videos --input .\videos.json `
  --option source="Authorised export" --option scope="same-video-cohort:v1" `
  --option observed_at=2026-10-02 --option sort_by=sample_views_median
```

For CSVs, map original headers explicitly: `id_column`, `title_column`,
`description_column`, `text_column`, `views_column`, `likes_column`,
`comments_column`, `published_column`, `channel_column`, `tags_column`.
`tags_delimiter` defaults to `|`; `delimiter` defaults to comma. JSON accepts a
list or `items`/`videos`/`comments`/`observations` array; `records_path=a.b` selects
another reviewed object path. This is not automatic support for every Takeout
or third-party export format. Export conversion must preserve semantics.

Canonical observation example (synthetic):

```json
[
  {"phrase":"#ukgarage", "kind":"hashtag", "metric":"reported_video_count",
   "value":1200, "unit":"videos", "country":"GB", "window":"all-time",
   "definition":"Reviewed provider total; not a sampled search result count"},
  {"phrase":"uk garage tutorial", "kind":"keyword", "metric":"studio_audience_interest",
   "value":"High", "unit":"provider_category", "window":"last-28-days"}
]
```

Observation rows may override provenance fields. CSV mappings additionally
support `phrase_column`, `metric_column`, `value_column`, `unit_column`,
`window_column`; fixed `metric`, `unit`, `kind`, `window` options support
single-metric exports. Native wide Studio reports should be explicitly converted
to long-form rows rather than mapping impressions to video views. Arbitrary
provider categories/scores retain their original names/units and are not
promoted into a common ranking score. `availability=unavailable` requires null.

For saved HTML use `item_selector`, and optional `phrase_selector`,
`value_selector`, `metric`, `unit`, `kind`, `window`. An `empty_selector` can
identify a genuine explicit empty-results screen. Without one, unmatched
selectors fail, including changed markup or an unrendered application shell.
No universal selectors are claimed for YouTube Studio, vidIQ or TubeBuddy.

```powershell
keywordmoves run youtube --operation trends-import --input .\multiTimeline.csv `
  --option search_property=youtube --option geography=GB `
  --option scope="YouTube Search:GB:2026-09:same-comparison" `
  --option observed_at=2026-10-02
```

The Trends CSV does not reliably prove which search property produced it, so
`search_property=youtube` is an operator attestation. Keep the original filter
settings and comparison set. Scores from the generic importer are cleared; its
relative-index evidence is preserved. `trends_operation=import-related` selects
the related-query importer. An unavailable Trends API is not substituted with
an undocumented private request [23].

### Snapshot comparison

```powershell
keywordmoves run youtube --operation compare --input .\before.json --input .\after.json
```

Only exact compatible numeric observations are compared: phrase, kind, provider,
collection scope, country, window, metric, unit and definition must match.
Approximate K/M/B displays, missing values, sample counters from changing search
samples and duplicate unscoped measurements are excluded/rejected. Output gives
net change, net change per elapsed day, and percentage change (null for zero
baseline). Decreasing counts remain negative; they can reflect removals or
source revisions rather than a negative posting rate. Changing rolling windows
are not comparable lifetime totals. Do not compare different view definitions.
API-derived comparisons require the applicable acceptance declaration.

## Other discovery avenues and actual coverage

| Avenue | Coverage in this patch / limitation |
| --- | --- |
| YouTube Studio Trends | Import reviewed top searches, audience-interest labels and Shorts content-gap observations. No claimed public endpoint for your personalised Trends tab [24]. |
| YouTube Studio Advanced Mode / Reach / retention | Native Analytics routes above plus explicit observation imports for other exported metrics; no made-up keyword-level impressions/CTR attribution. |
| vidIQ Keyword Research | Related/matching/questions and proprietary volume/competition are useful provider evidence. Import authorised reports/observations; no verified public developer API/website scraper is supplied here [25]. |
| TubeBuddy Keyword Explorer/Search Explorer | Import provider-scored keywords/tags and related terms with the provider's labels. Do not treat scores as YouTube-published confidence or demand. No authenticated extension API is reverse-engineered [26]. |
| Ahrefs YouTube Keyword Tool | Import YouTube-specific clickstream-based estimates; this does not turn the existing general Ahrefs API adapter into a verified YouTube API [27]. |
| YouTube Charts/music/podcasts/gaming | `popular` is the current API chart route, not exhaustive music/sound discovery. Import other reviewed chart ranks with category, country and interval intact. |
| Home/recommendations/related-video panels | Third-party SERP related searches are implemented. Personalised recommendation history and dedicated provider video-panel APIs are not silently equated to search demand. |
| Shorts sound/remix/topic pages | Traffic-source categories may report incoming traffic; there is no new global sound inventory or sound-popularity scraper. |
| Public subtitles and on-screen words | Authorised captions and supplied subtitles supported. No new OCR/ASR or anonymous transcript bypass. Feed authorised text into existing NLP extractors separately. |
| Community posts, live-chat, memberships and private videos | No new collection route. They require distinct access/schemas and are not inferred from public video samples. |
| Bulk Reporting API / researcher access / content-owner CMS | Useful separately approved routes, not newly implemented here. Do not claim partner/research access from an ordinary Data API key. |
| Google Ads, Google Search and Search Console | Existing plugins can supply cross-platform context; label it separately. Google Ads volumes and website search clicks are not organic YouTube query counts. |
| Local NLP + LLM brainstorming | Existing NLTK/spaCy/KeyBERT plugins can extract/rank imported text. LLM ideas remain proposals until verified; this module never silently starts one. |

## Limits, failures and privacy

Common options: `limit=50` (1..10000), `max_words=3` (1..5),
`include_keywords=true`, `include_tags=true`, `stopwords` (extra comma-separated
words), `max_occurrences=20` (0..200), `max_candidates=50000` (up to 100000),
`max_records=10000` (up to 50000), `max_input_bytes=5000000` (up to 20000000).
Recorded occurrence/support examples can truncate independently of exact counts;
flags identify this. Empty text can produce empty results, not known popularity.

The existing HTTP layer bounds requests, response bytes, timeouts and per-run
throttling. `max_requests` defaults to 10 here, can be explicitly set within the
transport's bounds; it is per command, not a persistent account quota ledger.
No redirects, automatic paid retries or cross-provider fallbacks occur. API
errors are cleanly surfaced; account/token errors and unavailable comments are
not zero-demand observations. Avoid debug logging of request secrets. Keys/tokens
are not copied into normal result metadata.

This is aggregate/content keyword analysis, not identification or sensitive
profiling of viewers/commenters. Text may still contain personal information.
Respect permissions, rate limits, platform contracts and data-subject/account
revocation requirements. The plugin does not publish anything or secretly
schedule collection. Third-party provider cost/availability can change.

## Tests and offline demonstration

```powershell
python -m pip install -e ".[dev,online]"
python -m pytest tests/test_youtube_analysis.py tests/test_youtube_api.py tests/test_youtube_providers.py tests/test_youtube_imports.py
python .\docs\examples\youtube-workflow.py
```

The example writes synthetic sample analysis, two observation snapshots, a
comparison and subtitle analysis to a new `youtube-demo` directory. It makes no
network requests, accepts no keys and refuses to overwrite an existing folder.
Tests use real HTTPX/HTML parsing with synthetic fixtures; no live API/provider
success, production entitlements or real popularity figures are claimed.

A focused Linux/Windows Python 3.10–3.13 workflow runs these tests, Ruff on new
files and the offline example. Live smoke tests are an explicit operator step
with their own credentials and quota/credit budget. A successful fixture test
cannot establish current HTML markup, account permissions or source accuracy.

## Sources checked

Primary documentation reviewed on 2 October 2026. Page/plan details can change;
recheck contracts before live deployment. Source statements here are paraphrased.

1. [YouTube search.list](https://developers.google.com/youtube/v3/docs/search/list)
2. [Video resource and snippet/statistics](https://developers.google.com/youtube/v3/docs/videos)
3. [Data API revision history](https://developers.google.com/youtube/v3/revision_history)
4. [Additional derived-metric/storage policies](https://developers.google.com/youtube/terms/derived-metrics-policy)
5. [API developer policies](https://developers.google.com/youtube/terms/developer-policies)
6. [Quota costs](https://developers.google.com/youtube/v3/determine_quota_cost)
7. [Channels list](https://developers.google.com/youtube/v3/docs/channels/list)
8. [Playlist items list](https://developers.google.com/youtube/v3/docs/playlistItems/list)
9. [Comment threads list](https://developers.google.com/youtube/v3/docs/commentThreads/list)
10. [Comments list](https://developers.google.com/youtube/v3/docs/comments/list)
11. [Captions list](https://developers.google.com/youtube/v3/docs/captions/list)
12. [Captions download](https://developers.google.com/youtube/v3/docs/captions/download)
13. [Analytics channel reports](https://developers.google.com/youtube/analytics/channel_reports)
14. [Analytics dimensions and HASHTAGS/YT_SEARCH](https://developers.google.com/youtube/analytics/dimensions)
15. [Keyword Tool API and YouTube types](https://keywordtool.io/api)
16. [SerpApi YouTube search](https://serpapi.com/youtube-search-api), [Shorts results](https://serpapi.com/youtube-shorts-results), [related searches](https://serpapi.com/youtube-related_searches)
17. [DataForSEO YouTube organic live advanced](https://docs.dataforseo.com/v3/serp/youtube/organic/live/advanced/)
18. [SerpApi provider result documentation](https://serpapi.com/youtube-video-results)
19. [Provider discussion of browser autocomplete](https://serpapi.com/blog/scrape-youtube-autocomplete-results-with-python/)
20. [Streamers YouTube actor input schema](https://apify.com/streamers/youtube-scraper/input-schema)
21. [YouTube comments actor and native output](https://apify.com/streamers/youtube-comments-scraper), [input schema](https://apify.com/streamers/youtube-comments-scraper/input-schema)
22. [Apify asynchronous Actor run API](https://docs.apify.com/api/v2/actors-runs-post)
23. [Google Trends help](https://support.google.com/trends/answer/4365533?hl=en)
24. [YouTube Studio Trends](https://support.google.com/youtube/answer/11962757?co=GENIE.Platform%3DDesktop&hl=en)
25. [vidIQ Keyword Research](https://support.vidiq.com/en/articles/9421214-keywords-research)
26. [TubeBuddy Keyword Explorer](https://www.tubebuddy.com/tools/keyword-explorer/), [Search Explorer](https://www.tubebuddy.com/tools/search-explorer/)
27. [Ahrefs YouTube Keyword Tool](https://ahrefs.com/youtube-keyword-tool)
28. [YouTube hashtag help](https://support.google.com/youtube/answer/6390658?hl=en)
29. [Analytics reports.query and required scopes](https://developers.google.com/youtube/analytics/reference/reports/query), rechecked 8 October 2026.
