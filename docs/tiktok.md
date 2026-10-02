# TikTok keyword discovery and evidence analysis

Part of Kieran Simkin's KeywordMoves / DanceFlow workflow.
Provider contracts reviewed **2 October 2026**. This module adds the `tiktok`
keyword plugin without changing either generative LLM runtime. No credentials,
network calls, model downloads or remote jobs are triggered by listing plugins.

## Scope and the important distinctions

The module has **20 operations** spanning official APIs, third-party APIs,
public-page extraction, reviewed file imports and local comparison. It does not
claim unrestricted access to everything TikTok knows or that every commercial
analytics vendor is integrated. Sources requiring an account or approval still
require that access, and the experimental HTML route may be blocked or outdated.

Keep these evidence families separate:

- **Reported hashtag aggregates:** numbers explicitly returned by a source for
  a tag, with country/filter/window and capture date. A video sample is not a total.
- **Search evidence:** suggested search phrases, estimated monthly searches,
  native popularity indices and personal Creator Search Insights observations.
  A phrase is not automatically a confirmed hashtag.
- **Sample associations:** engagement of collected videos carrying a tag or
  caption keyword, plus co-occurrence and sound associations. Whole-video
  engagement cannot be attributed independently to each hashtag.
- **Account, comment and advertising evidence:** account totals, comment language,
  or ad-library matches. None is relabelled as global organic hashtag demand.

`KeywordCandidate.score` remains unset. A result can be sorted by one specific
measurement, but the adapter does not invent an overall popularity, organic
competition, confidence, causal uplift or banned-hashtag score.

## Installation

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

The existing `online` extra supplies HTTPX and Beautiful Soup. Local JSON/CSV
analysis needs neither library; HTML extraction needs Beautiful Soup and live
requests need HTTPX. No spaCy, NLTK, KeyBERT, PyTorch, OpenAI or API key is needed
for local analysis. Extraction from text is a transparent lexical pass, not a
silent invocation of a generative model.

## Operations and access requirements

| Operation | Input and result | Access route |
| --- | --- | --- |
| `extract` | Files/directories/inline text; literal hashtags and optional caption phrases. | Local; no existence/popularity verification. |
| `import-videos` | JSON/CSV video records; hashtag, keyword and engagement analysis. | Reviewed local export. |
| `import-comments` | JSON/CSV comment/reply records; audience language and comment likes. | Reviewed local export; not video usage. |
| `import-hashtags` | Canonical or Clockworks-native hashtag counts, related tags and trend/audience fields. | Reviewed local export. |
| `import-observations` | Explicit phrase, metric, value, unit, date, country and scope. | Native insights or other reviewed reports. |
| `import-html` | Saved rendered HTML and user-reviewed CSS selectors. | Local extraction; no browser automation. |
| `compare` | Two saved TikTok result JSON files, older first. | Compatible, exact aggregate measurements only. |
| `oembed` | Canonical public video URLs; caption keywords/hashtags. | TikTok's documented oEmbed; no engagement counts. |
| `public-hashtag` | One exact tag; explicitly present page-state counts. | Experimental public HTML; opt-in and robots check. |
| `suggestions` | One seed search phrase; related phrases, optionally search estimates. | Keyword Tool API and account key. |
| `metrics` | A keyword list; search estimates and labelled proxy fields. | Keyword Tool API and account key. |
| `display-videos` | Paginated public videos of the authorising user. | TikTok Display API, user token and `video.list`. |
| `display-query` | Up to 20 explicit video IDs from the authorising user. | TikTok Display API; not arbitrary public-video access. |
| `display-user` | Authorised profile/bio and explicitly requested account metrics. | Display API, relevant `user.info.*` scopes. |
| `research-videos` | Hashtag, keyword, username, sound/effect/video ID and date filters. | Approved eligible Research API project. |
| `research-comments` | Comments on one video OR replies to one comment. | Approved Research API project; no recursive crawl. |
| `research-user` | One public username's available profile/account fields. | Approved Research API project. |
| `commercial-ads` | Matching ad-library IDs, dates/status and reported reach display. | Separately approved Commercial Content API. |
| `apify-start` | Explicitly submit one of six collection presets. | Apify key, paid opt-in and requested charge ceiling. |
| `apify-fetch` | Fetch an existing run or dataset with provenance. | Apify key; never starts/restarts a job. |

Official endpoint references: [Display list][display-list], [Display query][display-query],
[profile][display-user], [Research video query][research-videos],
[Research comments][research-comments], [Research user][research-user],
[ad library][commercial-ads] and [oEmbed][oembed].

**The Research API is not a marketing shortcut.** TikTok explicitly says creators,
advertisers and commercial users are not eligible for Research Tools. A developer
account alone is insufficient; an eligible, specific project must be approved.
This module's `research_approved=true` is a declaration, not a way to grant access.
Use these operations only within an actually approved project. The Display API,
authorised exports and appropriately licensed third-party services are different
routes. TikTok also documents archived video-query data: new videos may take up
48 hours to appear and statistics up to 10 days to update. [Research FAQ][research-faq]

## Known hashtags and reported popularity

### Submit a hashtag-analytics collection

The `hashtag-analytics` preset uses **`clockworks/tiktok-ads-scraper`**, whose
current product is TikTok Hashtag Analytics. Despite the actor slug, this preset
is not a TikTok Ads Manager campaign-report API. Its documented input takes tags,
a country and a time range; its example output contains all-time and period
post/view counts, related tags, trend points and audience/industry fields.
[Actor and output example][hashtag-analytics]

```powershell
$env:APIFY_TOKEN = "YOUR_APIFY_TOKEN"
keywordmoves run tiktok `
  --operation apify-start `
  --keyword "ukgarage" `
  --keyword "brightonmusic" `
  --option actor=hashtag-analytics `
  --option country=GB `
  --option period=7 `
  --option allow_paid=true `
  --option max_charge_usd=1
```

**This command starts a paid-capable job.** Read the actor's current pricing and
terms before executing it. The requested ceiling is sent as Apify's
`maxTotalChargeUsd`, alongside a timeout and `restartOnError=false`. It is not a
promise about all charges elsewhere in your account. Jobs return a `run_id`;
submission is not completion. [Apify start-run contract][apify-start]

After the same run finishes:

```powershell
keywordmoves run tiktok `
  --operation apify-fetch `
  --option run_id=RETURNED_RUN_ID `
  --option dataset_kind=hashtags `
  --option "scope=hashtag-analytics:GB:7:same-actor-build-and-settings" `
  --option sort_by=reported_post_count `
  --option limit=100
```

The run's `finishedAt` is the collection-time proxy; `retrieved_at` records the
fetch time separately. For more precise observations, use row-level capture dates
in a reviewed import. A pending job returns its status without fabricating an
empty completed collection. A failed job raises an explicit error.

The parser maps:

| Native field | Output evidence |
| --- | --- |
| `publishCntAll` / `videoCount` | `reported_post_count` |
| `videoViewsAll` / `viewCount` | `reported_view_count` |
| `publishCnt` | `period_post_count` |
| `videoViews` | `period_view_count` |

These are **source-reported** totals, not independently audited platform-wide
counts. Preserve the record's `countryCode`, `period`, source and scope; do not
assume a country's returned metric applies to every region or viewer.
`relatedHashtags` and `recList` produce related-tag candidates with their parent
tag and list positions, without borrowing the parent's counts. The plugin never
recursively buys more lookups for every discovered tag.

Trend, longevity, country/industry, promotion status and audience age/interest/
country objects remain under `native_dimensions`. Native scores are not coerced
to percentages: the provider's example includes interest and country scores above
100. These fields describe the source's categories and scope, not inferred
attributes of particular people. [Native output example][hashtag-analytics]

### Other collection presets

The remaining presets use `clockworks/tiktok-scraper`:

| `actor` option | `--keyword` values | Main actor input |
| --- | --- | --- |
| `hashtag-videos` | Tags with or without `#`. | `hashtags` |
| `keyword-videos` | Search phrases. | `searchQueries`, `searchSection=/video` |
| `profile-videos` | Usernames, optionally starting with `@`. | `profiles`, public video section |
| `video-details` | Full canonical video URLs. | `postURLs` |
| `related-videos` | Full canonical video URLs. | `postURLs`, `scrapeRelatedVideos=true` |

These are third-party scraper routes, not first-party TikTok analytics. Access
constraints and sampling still apply. [Actor input schema][video-input]

For a bounded sample of known-tag videos:

```powershell
keywordmoves run tiktok --operation apify-start `
  --keyword "ukgarage" `
  --option actor=hashtag-videos `
  --option results_per_seed=50 `
  --option allow_paid=true `
  --option max_charge_usd=1

keywordmoves run tiktok --operation apify-fetch `
  --option run_id=RETURNED_RUN_ID `
  --option dataset_kind=videos `
  --option "scope=hashtag-videos:ukgarage:50:same-settings" `
  --option include_keywords=true `
  --option sort_by=sample_video_views_median
```

`results_per_seed` is per tag/query/profile, **not a global total or spending cap**.
It does not bound every related-video expansion inside the remote actor. The
provider's charge ceiling and actor timeout are separate safeguards.

Only `keyword-videos` accepts `video_sort` (`MOST_RELEVANT`, `MOST_LIKED`, `LATEST`),
`video_date_filter` (`ALL_TIME`, `PAST_24_HOURS`, `PAST_WEEK`, `PAST_MONTH`,
`LAST_3_MONTHS`, `LAST_6_MONTHS`) and `related_searches=true`. The last setting asks
the actor to collect “Others also searched for” in its **raw dataset**; the module
does not assume an undocumented stable output field for those words. Review and
map them through `import-observations` rather than treating them as known hashtags.

`comments_per_post=0` and `replies_per_comment=0` are defaults. Nonzero reply
collection requires nonzero comment collection. Comments can be stored in a
separate actor dataset: fetch it explicitly with `dataset_kind=comments`,
`dataset_id`, its collection date and scope. An arbitrary dataset URL embedded in
a response is never followed automatically. Downloading videos, covers, avatars,
slideshow images, music covers and subtitles is disabled, as are the actor's AI
video summary/description options. [Input options][video-input]

`actor_timeout` defaults to 300 seconds (1–3600); `actor_build` is an optional
reviewed build identifier. Record the submitted input and selected build from the
job's result metadata for reproducibility. Country/time-range options are specific
to `hashtag-analytics` and are rejected on video presets rather than ignored.

## TikTok keyword suggestions and search estimates

```powershell
$env:KEYWORDTOOL_API_KEY = "YOUR_API_KEY"
keywordmoves run tiktok --operation suggestions `
  --keyword "uk garage" `
  --option country=GB `
  --option language=en `
  --option metrics=true `
  --option sort_by=estimated_search_volume `
  --option limit=50
```

For known search phrases:

```powershell
keywordmoves run tiktok --operation metrics `
  --keyword "uk garage" --keyword "brighton music" `
  --option country=GB --option language=en
```

Keyword Tool's TikTok route supports **search suggestions**, not its
Instagram-style `type=hashtags` mode. Results are labelled
`not-a-confirmed-hashtag`; adding `#` to a search phrase does not establish usage.
The provider's monthly search estimates, history and partial-result notes are
preserved. CPC and competition become `google_ads_cpc_proxy` and
`google_ads_competition_proxy`, not TikTok organic competition. Estimated volume
is explicitly approximate and excluded from exact snapshot-growth calculations.
[Keyword Tool's current API documentation][keywordtool]

The existing provider adapter accepts `api_key` or `KEYWORDTOOL_API_KEY`;
`country` is required, `language` defaults to `en`, and optional `currency` and
`sandbox` use that provider's contract. `metrics=true` can increase chargeable
work. `limit` trims output locally and cannot universally cap upstream billing.

## Own-account analysis through the Display API

```powershell
$env:TIKTOK_ACCESS_TOKEN = "YOUR_AUTHORISED_USER_ACCESS_TOKEN"
keywordmoves run tiktok --operation display-videos `
  --option pages=3 --option page_size=20 `
  --option include_keywords=true
```

`display-videos` calls `POST /v2/video/list/`, requires `video.list`, and lists
public videos of the **authorising account**. It is not a public hashtag search.
`display-query` calls `POST /v2/video/query/` with up to 20 repeated `--keyword`
video IDs. IDs are preserved exactly; missing returned IDs are recorded, not
interpreted as banned content. [List][display-list] / [query][display-query]

`display-user` uses `GET /v2/user/info/`. Default `profile_fields=display_name`
requests the basic field. For example:

```powershell
keywordmoves run tiktok --operation display-user `
  --option "profile_fields=display_name,bio_description,username,is_verified,follower_count,following_count,likes_count,video_count"
```

Your app and token must have the appropriate `user.info.basic`,
`user.info.profile` and `user.info.stats` permissions for the selected fields.
Account totals remain profile metadata rather than being attached to every tag.
Watch time, audience retention, search traffic attribution and complete
per-hashtag performance are **not supplied by these Display API calls**; import
reviewed Studio/owned-account reports with their real scope. [Profile API][display-user]

OAuth consent, app review, access-token creation/refresh and secure secret storage
are external setup steps. Do not paste real tokens into example files, Git or
support logs. `access_token` is an explicit per-run alternative to the environment.
Environment secret injection avoids exposing a token in process arguments, though
literal shell assignments may also enter shell history.

## Approved Research API operations

Only for an actually approved eligible research project, not ordinary commercial
keyword research:

```powershell
$env:TIKTOK_RESEARCH_ACCESS_TOKEN = "YOUR_APPROVED_RESEARCH_TOKEN"
keywordmoves run tiktok --operation research-videos `
  --keyword "ukgarage" --keyword "brightonmusic" `
  --option research_approved=true `
  --option start_date=2026-09-01 --option end_date=2026-09-30 `
  --option query_field=hashtag_name `
  --option region=GB `
  --option pages=2 --option page_size=100
```

Dates are ISO `YYYY-MM-DD` on the CLI and converted to API `YYYYMMDD`; start/end
must be ordered and at most 30 days apart. `query_field` supports `hashtag_name`,
`keyword`, `username`, `music_id`, `effect_id`, `video_id`. Multiple seeds form an
OR; optional `region` adds an AND condition. Research `region_code` identifies
creator registration country, not the viewers' location. `is_random=true` is an
explicit provider request, not a guarantee of population representativeness.
`include_transcript=true` requests existing `voice_to_text`; it performs no new
speech transcription. [Video query contract][research-videos]

For comments use `research-comments` with exactly one `video_id` or `comment_id`.
The latter requests replies. It never recursively follows all replies; comment
IDs, not their parent video IDs, drive deduplication. Profile research uses
`research-user --option username=...`. Both require `research_approved=true` and
the Research token. The three Research operations call only their documented
endpoints and return bounded samples. [Comments][research-comments] / [user][research-user]

## Advertising-language research

`commercial-ads` uses the separately approved Commercial Content API:

```powershell
$env:TIKTOK_COMMERCIAL_ACCESS_TOKEN = "YOUR_APPROVED_COMMERCIAL_CONTENT_TOKEN"
keywordmoves run tiktok --operation commercial-ads `
  --keyword "independent music" `
  --option commercial_approved=true `
  --option country=GB `
  --option start_date=2026-09-01 --option end_date=2026-09-30 `
  --option search_type=exact_phrase --option page_size=10
```

Select a country/date supported by your current library access; this example does
not grant access or guarantee coverage. TikTok validates its rolling date/region
availability. The adapter returns bounded ad matches and retains ad IDs,
first/last-shown dates, status and raw reach display. It does **not** request or
claim to extract unrestricted ad-copy text from this endpoint. Rounded/ranged
reach is not summed across overlapping ad audiences. `fuzzy_phrase` is the other
supported search type. These matches are not organic search volume or hashtag
popularity. [Commercial Content query contract][commercial-ads]

## Native website and in-app routes

### Public hashtag page and oEmbed

```powershell
keywordmoves run tiktok --operation public-hashtag `
  --keyword "ukgarage" --option allow_unofficial=true
```

This performs a robots-policy check, then requests
`https://www.tiktok.com/tag/<encoded-tag>`. It recognises two **experimental**
embedded JSON shapes (`__UNIVERSAL_DATA_FOR_REHYDRATION__` challenge detail and
legacy `SIGI_STATE` challenge module), requires an exact matching tag and an
actual count, and fails on unavailable/challenged/changed data. It does not execute
JavaScript, sign private endpoints, submit cookies, solve challenges, rotate
proxies, follow redirects or turn a login page into zero demand.

**No successful current live TikTok-page response was verified when building this
patch.** The parser is tested against explicit synthetic shapes only. A robots
allowance is not a grant of account access or a substitute for reviewing terms.

For a documented public caption route:

```powershell
keywordmoves run tiktok --operation oembed `
  --keyword "https://www.tiktok.com/@example/video/1234567890123456789"
```

Replace that placeholder with an actual public video URL. The adapter only
accepts full canonical video URLs, not short links or arbitrary endpoints. It
extracts the oEmbed title/caption without executing returned embed HTML. oEmbed
does not return view/like/share statistics. [TikTok oEmbed][oembed]

### Creative Center, Search Insights, Studio and other providers

| Avenue | Useful evidence | Support and limitations here |
| --- | --- | --- |
| Creative Center Trends | Trending/rising hashtags, country/time filters, related tags, sounds and videos. | Hashtag-specific analytics via the Apify preset; reviewed lists/indices via JSON/CSV/HTML import. No claim of a live unrestricted global trending-list API. |
| Creator Search Insights | Search topics, search popularity, content gaps and relevant related searches. | Explicit observed metrics via import; retain personalised account/filter/time scope. No verified direct in-app API client. |
| TikTok Studio / owned reports | Search terms, traffic-source breakdown, watch/completion metrics where your account exposes them. | Import actual keyword observations or normalised video records. Extra per-video fields not in the canonical sample schema must be retained as explicit observations. |
| Native search suggestions / “Others also searched for” | Search phrases; visible hashtag labels and sampled results. | Keyword Tool search API, actor raw related-search collection, or saved reviewed observations. No private signed suggestion endpoint is claimed. |
| Captions, subtitles and spoken words | Hashtags, phrases and associations with available engagement. | Captions/hashtags and optional supplied transcript text; no media download, OCR or new ASR. |
| Public profiles and comments | Bio terms, community language, requested comment/reply samples. | Approved APIs or third-party collection/import, with comment/profile counts separated from videos. |
| Sounds, effects and related videos | Associated music IDs/names, related content, effect-filtered research. | Sample sound associations, Research sound/effect filters and related-video preset; not a universal sound popularity database. |
| Ads, TikTok One, Business tools and Shop | Ad phrasing, advertiser insights, commerce/product signals. | Documented ad-library query; other account-specific reports through explicit observations. No invented Marketing/Shop endpoint or claims of complete organic coverage. |
| Exolyt, MaveKite and other licensed tools | Tool-specific hashtag, sound, creator, historical and audience measures. | Reviewed exports with mappings. These are not additional live adapters; each tool's current export fields/access must be reviewed. |
| TikAPI, other scraper APIs, mobile/browser libraries | Alternative collection infrastructure. | Not integrated in this patch. No unverified endpoint, private session requirement or signing workflow is presented as supported. |
| TikTok data exports / portability | User-authorised content and account records. | Map appropriate content to canonical imports. Raw arbitrary account archives are not automatically ingested; DMs/private-contact data are outside this module. |

Sources: [Creator Search Insights announcement][creator-search],
[Creative Center overview][creative-center], [TikTok Studio][studio],
[API for Business][business-api], [Exolyt features][exolyt],
[MaveKite features/exports][mavekite]. Not all these interfaces publish a general
live API contract, and none is silently scraped by the generic import operation.

## Local imports and cross-provider provenance

```powershell
keywordmoves run tiktok --operation import-videos `
  --input .\videos.json `
  --keyword "ukgarage" `
  --option source="Reviewed TikTok video export" `
  --option "scope=hashtag:ukgarage:GB:latest:50-post-sample" `
  --option observed_at=2026-10-02 `
  --option include_keywords=true
```

`source`, `scope` and `observed_at` are mandatory for observation/media imports.
Capture date means when the data was actually observed, not when a later file
happens to be opened. Row-level observation provenance takes precedence.
`--keyword` on video/comment imports marks known tags for inspection; it does not
suppress other discovered candidates. An absent seed is only
`not-observed-in-this-sample`, never globally unknown, unused or banned.

JSON may be a record array, a supported `data.videos`/`data.comments` response, or:

```json
{
  "schema": "keywordmoves-tiktok/v1",
  "videos": [
    {"id": "101", "caption": "Synthetic #UKGarage example", "view_count": 1000,
     "like_count": 80, "comment_count": 10, "share_count": 20,
     "created_at": "2026-10-01T12:00:00Z", "music_id": "201"}
  ]
}
```

All numbers above are **synthetic**. Wrapper keys also support `comments`,
`hashtags`, `observations` as appropriate. Other exports can use a dot-separated
`records_path`, such as `report.items`; array indexing in paths is not supported.
Only JSON and CSV record files are accepted. Multiple inputs are processed without
joining unrelated documents; duplicate resolved paths are read once.

Canonical media fields: `id`, `caption`, `hashtags` (array), `created_at`,
`view_count`, `like_count`, `comment_count`, `share_count`, `save_count`,
`repost_count`, `duration`, `username`, `music_id`, optional `transcript`.
Aliases for documented TikTok/Clockworks fields are implemented; arbitrary
third-party shapes require an explicit mapping. Exact 64-bit IDs/counts never
pass through floating point. In CSV, list-valued cells must contain JSON arrays.

A generic observation suitable for Creator Search Insights or another report:

```json
{
  "phrase": "independent music",
  "metric": "search_popularity_index",
  "value": 72,
  "unit": "index_0_100",
  "platform": "TikTok",
  "country": "GB",
  "window": "7",
  "notes": "Synthetic example only; use the actual source's scale and scope."
}
```

The importer does not certify that an index is on a particular scale. Keep the
metric/unit actually displayed, plus the signed-in/personalised scope. Don't
map an index, rank, CPC or reach range into a post-count column. Set `kind=hashtag`
or use `import-hashtags` for actual tags.

CSV column mappings are JSON objects from canonical output names to source
column names, e.g. `{"phrase":"Topic","metric":"Measurement","value":"Amount","unit":"Units"}`.
A missing mapped column is an error, not an invented null or zero. Missing numeric
cells remain null; explicit zero remains zero. `availability=unavailable` permits
no measured count, including zero. Cross-platform records are rejected.

Compact English counts such as `1.2M` are expanded for display/sorting and marked
in `approximate_metrics`. Other ambiguous locale formats require a reviewed
normalisation; the parser does not guess what `1,2` means. Numbers known to be
estimates should be marked approximate even when displayed as integers.

### Saved HTML

Use `import-html`, `record_selector`, `field_selectors` (JSON field-to-CSS mapping)
and `dataset_kind=hashtags|observations|videos|comments`. Optional `empty_selector`
identifies a reviewed genuine no-results marker. Without it, missing results are
an explicit failure. Every configured field must match; `:self` takes the record's
text. Selector text extraction does not automatically extract HTML attributes.

The example fixture is deliberately **not** Instagram/TikTok's current markup:

```powershell
keywordmoves run tiktok --operation import-html `
  --input .\tests\fixtures\tiktok\rendered.html `
  --option record_selector=article.tag-result `
  --option 'field_selectors={"hashtag":"h2","reported_post_count":".posts","country":".country","period":".period"}' `
  --option source="Synthetic reviewed layout" `
  --option "scope=synthetic:GB:7" --option observed_at=2026-10-02
```

Windows PowerShell 5.1 may alter nested native-command quotes. For complex JSON
mappings, prefer the included Python API pattern with a dictionary rather than
weakening parsing or silently dropping a mapping.

## What the analyser calculates

Per candidate it retains distinct-record count, document frequency and, for each
available metric, **availability count, sum, mean and median**. Optional keyword
phrases require literal adjacency: punctuation, stopwords, mentions, hashtags and
line breaks form boundaries. Names are not inferred from capitalisation and
common words are not silently lemmatised.

For video samples, the supported measurements are views, likes, comments, shares,
saves/favourites and reposts. Mean/median durations, available dates, sample counts
within the last 7/30 days, future-date flags, distinct available author count,
sounds (IDs and observed names), and video/slideshow breakdowns are retained.
Unavailable timestamps produce unknown activity, not a zero posting rate.

`sample_interactions_per_view` is:

```text
sum(likes + comments + shares) / sum(views)
```

using only records where all four measurements exist. Its paired-record count
and summed view denominator are emitted. Saves/reposts are not silently substituted
when likes/comments/shares are missing; a zero denominator yields null. This is
an association with videos carrying a term, not unique audience engagement or
independent hashtag-attributed performance.

Co-occurring tags retain shared-record count, conditional ratio, Jaccard and lift.
The top 20 neighbours and sounds are kept. These describe the selected sample,
not market prevalence or a causal effect. Comments/profile/reference text use
different count names and cannot inflate sampled video metrics.

Duplicate IDs or canonical video URLs merge missing fields without adding duplicate
counts; conflicting observations retain the first value and flag conflicts.
Records without usable IDs remain separate even if captions are identical.
Hashtag repetitions within one caption count once toward document frequency.
Bounded character-offset examples retain their source field and sample index.

## Snapshot comparisons

Save two `import-hashtags`/`apify-fetch`/observation results as UTF-8 JSON, then:

```powershell
keywordmoves run tiktok --operation compare --input .\before.json --input .\after.json
```

`compare` requires matching platform, phrase, source, metric, unit, geography,
explicit scope and window. It calculates net change, net change/day and percentage
change only for compatible exact reported aggregate counts (and any explicitly
exact comparable search measurement). Estimated Keyword Tool volume and compact
rounded displays are excluded. A zero baseline produces null percentage; negative
changes are retained. Nothing is extrapolated into a forecast or gross new-post
rate. Duplicate ambiguous measurements, backwards dates, unverified schemas and
no comparable pairs are explicit errors.

A result cut by `limit` may omit potential pairs. Increase the output limit when
saving comparison snapshots, and retain source/settings rather than comparing
samples from different countries or collection methods.

## Configuration and limits

| Group | Options / defaults |
| --- | --- |
| Result | `limit=50` (1–1000), `sort_by=discovery`. |
| Sort choices | `reported_post_count`, `reported_view_count`, `period_post_count`, `period_view_count`, `estimated_search_volume`, `sampled_video_count`, `sample_video_views_median`, `sample_interactions_per_view`. Missing values sort last. |
| Analysis | `include_keywords=false` for video samples, true for text/comments/profile; `include_transcript=false`; `max_words=3` (1–5); comma-separated extra `stopwords`; `max_occurrences=10` (0–100); `max_records=2000` (1–10000). |
| Files | `max_input_bytes=5000000` (1024–20000000), combined across record/reference input files; reference directory cap 1000 files; maximum 10000 distinct candidates. |
| HTTP | `max_requests=5` (1–20), `max_response_bytes=2000000` (1024–10000000), `timeout=30` seconds (1–120), `min_interval=1` second. Minimum effective request spacing is one second. |
| API pagination | `pages=1` (1–10); `page_size=20`, capped at 20 for Display, 100 for Research and 10 for ads. `cursor` and, where supported, `search_id` resume the provider query. |
| Apify dataset | `page_size=100` (1–1000), `pages=1` (1–10), `offset=0`; the status request counts against the same HTTP budget. |
| Apify submission | `actor=hashtag-videos`, `results_per_seed=50` (1–1000) for video presets; explicit `allow_paid=true`, `max_charge_usd` >0 and <=100; `actor_timeout=300`; optional `actor_build`. |
| Hashtag analytics | Required two-letter `country`, `period=7` (`7`, `30`, `120`, `365`, `1095`). |

Unsupported operation-specific options are errors. Sampling/pagination budgets
stop further calls and report `more_available`, `next_cursor`/`next_search_id` or
`next_offset`; these are not market completeness claims. A full final dataset page
is conservatively marked as possibly having more data. The module never silently
retries paid requests or switches providers after a failure. Shared transport
uses approved HTTPS hosts, no redirects, no environment proxy credentials and
bounded decoded responses. Provider bodies and credentials are not included in
normal error messages, but external debug logging is outside that guarantee.

## Tests and offline demonstration

```powershell
python -m pip install -e ".[dev,online]"
python -m pytest tests/test_tiktok_analysis.py tests/test_tiktok_api.py tests/test_tiktok_imports.py
python .\docs\examples\tiktok-workflow.py
```

The example writes fabricated sample analysis, two hashtag snapshots and a
comparison under `tiktok-demo`. It does not send data, start jobs, write API keys
or overwrite existing demo result files. Test HTTP responses and HTML are
synthetic; actual HTTPX serialization and Beautiful Soup parsing are exercised.
No live authenticated TikTok/Keyword Tool/Apify request or paid collection was used
for validation. This demonstrates adapter behaviour, not current account access,
provider uptime, real-world completeness or a model's prediction quality.

CI runs credential-free TikTok tests, focused linting and the offline example on
Linux/Windows and Python 3.10–3.13. A production smoke test still requires your
legitimate access and explicit approval for any billable collection. Missing
optional packages are reported only when their route is used.

## Research references

Links identify the contract/interface reviewed, not a promise that an account has
access. Dynamic sites and actor schemas can change; record versions and recheck
contracts when a provider reports an error. All accessed 2 October 2026.

[display-list]: https://developers.tiktok.com/doc/tiktok-api-v2-video-list/
[display-query]: https://developers.tiktok.com/doc/tiktok-api-v2-video-query/
[display-user]: https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info/
[research-videos]: https://developers.tiktok.com/doc/research-api-specs-query-videos/
[research-comments]: https://developers.tiktok.com/doc/research-api-specs-query-video-comments/
[research-user]: https://developers.tiktok.com/doc/research-api-specs-query-user-info/
[research-faq]: https://developers.tiktok.com/doc/research-api-faq
[commercial-ads]: https://developers.tiktok.com/docs/en/commercial-content-api-query-ads
[oembed]: https://developers.tiktok.com/doc/embed-videos/
[keywordtool]: https://keywordtool.io/api
[hashtag-analytics]: https://apify.com/clockworks/tiktok-ads-scraper
[video-input]: https://apify.com/clockworks/tiktok-scraper/input-schema
[video-output]: https://apify.com/clockworks/tiktok-scraper/output-schema
[apify-start]: https://docs.apify.com/api/v2/actors-runs-post
[creator-search]: https://newsroom.tiktok.com/en-us/creator-search-insights
[creative-center]: https://ads.tiktok.com/resources/help/article/creative-center?lang=en
[studio]: https://support.tiktok.com/en/using-tiktok/creating-videos/creator-tools-on-tiktok
[business-api]: https://business-api.tiktok.com/portal
[exolyt]: https://exolyt.com/features
[mavekite]: https://www.mavekite.com/
