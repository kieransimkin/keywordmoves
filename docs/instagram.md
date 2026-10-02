# Instagram hashtag and keyword research

KeywordMoves `instagram` is a keyword plugin, not an LLM runtime. It combines
explicitly selected collection routes with a local, source-labelled analyser.
No generative model, Instagram password, login-cookie import or account posting
is required. Generative expansion remains in the existing LLM plugin layer.

This guide and the adapter contracts were researched on **2 October 2026**.
The network implementations have automated HTTP contract tests with synthetic
responses, not live-account certification. Access, response fields, actor builds
and commercial plans can change. See [Sources](#sources-and-contract-references).

## The important distinction: what “popular” means

A hashtag with many posts is not necessarily searched often, relevant to your
reference text, or effective on your account. This plugin keeps these separate:

| Measurement | What it describes | What it does not establish |
| --- | --- | --- |
| `reported_post_count` | A source's reported supply of posts using a tag. | Searches, unique creators, audience size, unique viewers or guaranteed complete coverage. |
| `estimated_search_volume` | Keyword Tool's estimated monthly Instagram search volume. | Meta first-party counts or the number of hashtagged posts. |
| `sampled_post_count` | Distinct media IDs containing a tag in the downloaded sample. | The global hashtag population. |
| Mean/median likes, comments, views and other available counts | Engagement on the returned media sample. | Per-hashtag attribution, causal benefit, or a random representative sample. |
| Co-occurrence | Tags that appear together in the sampled captions or source-provided tag lists. | Synonyms, endorsement, demand or automatic suitability for your content. |
| Snapshot deltas | Changes between comparable dated numeric observations. | Gross new posts or a prediction of future growth. |

**There is no invented universal popularity score.** `KeywordCandidate.score`
remains `null`. To order a single-source result by a measurement, use
`--option sort_by=reported_post_count`, `estimated_search_volume`,
`sampled_post_count`, or `sample_likes_plus_comments_median`. The default
`discovery` preserves discovery order (local samples put more frequent tags
first). Unknown or conflicting numeric observations sort last. Sorting never
makes measurements from different sources comparable. For Keyword Tool,
sorting operates on the bounded result set returned by the existing adapter.

Meta's documented IG Hashtag object exposes `id` and `name`, not a global
`media_count`. The official route therefore resolves a tag and analyses returned
media without claiming a total hashtag count. Meta's hashtag search is limited
to **30 unique hashtags per professional account in a rolling seven-day window**.
It requires the Facebook Login configuration and appropriate approved public
content access; Instagram Login is not an equivalent hashtag-search route. [1–5]

## Install and list operations

From an updated checkout with this patch applied:

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

The existing `online` extra provides HTTPX and Beautiful Soup. Registry listing,
local text/JSON/CSV analysis and snapshot comparison do not import either library.
Only HTML operations need Beautiful Soup. No extra spaCy, NLTK, KeyBERT or OpenAI
install is needed to use this module.

The plugin exposes **15 operations**:

| Route | Operations | Required access |
| --- | --- | --- |
| Meta | `hashtag`, `quota`, `account`, `media-insights`, `comments` | Appropriate professional-account token, account ID, explicitly selected Graph API version and permissions. |
| Keyword Tool | `suggestions`, `metrics` | Keyword Tool API plan/key. Not an Instagram-owned API. |
| Apify | `apify-start`, `apify-fetch` | Apify token; starting a remote job additionally requires explicit paid-use opt-in. |
| Local | `extract`, `import-media`, `import-hashtags`, `import-html`, `compare` | Reference material or authorised exports and, for observations, provenance/date/scope. |
| Public webpage | `public-page` | Explicit `allow_web=true`, a permissive robots policy and usable public HTML. Experimental, not a guaranteed alternative to authentication. |

## 1. Find literal tags in reference text

```powershell
keywordmoves run instagram `
  --operation extract `
  --input .\reference.txt `
  --option include_keywords=true `
  --option limit=100
```

Inline text and multiple files are also supported:

```powershell
keywordmoves run instagram --operation extract --option "text=Brighton bass music #UKGarage #Brighton"
```

The analyser recognises Unicode letters, numbers, combining marks and underscores,
preserves source character offsets, normalises Unicode composition and lowercase,
and combines repeated occurrences into one candidate per tag. It does not join
separate words to manufacture a tag. Its 100-character maximum is a local safety
limit, not a promise about every character accepted by Instagram.

Tags found only in reference material are labelled **`reference-only`**, not
verified as existing on Instagram. `include_keywords=true` also returns literal
caption/reference words and adjacent bigrams using a small English stoplist.
Those candidates have `instagram-caption-keyword` relationships, not hashtag
labels. This simple method does not infer named entities or meaning.

For richer reference/caption phrases, run the existing `spacy`, `nltk`, or
`keybert` plugin separately. Reference proposals can be used as discovery seeds;
they are not evidence of a known or popular hashtag until an appropriate source
returns them. This plugin does not silently call an LLM or another extractor.

## 2. Discover related hashtags and estimated demand

Keyword Tool's documented API provides Instagram suggestions and estimated
volume. Its CPC and competition fields are Google Ads-derived proxy metrics,
not Instagram organic difficulty. Do not use those fields to rate how hard it
will be to rank on Instagram. [10]

```powershell
$env:KEYWORDTOOL_API_KEY = "YOUR_API_KEY"

keywordmoves run instagram `
  --operation suggestions `
  --keyword "ukgarage" `
  --option country=GB `
  --option language=en `
  --option metrics=true `
  --option sort_by=estimated_search_volume `
  --option limit=50
```

The Instagram wrapper explicitly requests `type=hashtags`, not account names.
Suggested tags are labelled `third-party-suggestion`; the module does not claim
to have independently resolved every returned name with Meta.

To measure an existing list:

```powershell
keywordmoves run instagram `
  --operation metrics `
  --keyword "ukgarage" `
  --keyword "bassmusic" `
  --keyword "brightonmusic" `
  --option country=GB `
  --option currency=GBP
```

A metrics-only query is labelled `unverified-metric-query`. Returning zero or
null for a requested tag does not verify that the tag exists or is prohibited.
Per-keyword monthly values and their month/year labels are preserved when the
provider supplies them. Missing values remain null.

Credentials: `KEYWORDTOOL_API_KEY` or `--option api_key=...`. Other supported
options are `country` (required two-letter code), `language` (two letters;
default `en`), `currency` (three letters; default `USD`), `sandbox=true`,
`metrics=true` for suggestions, and the shared transport/output options.
The existing adapter currently accepts one suggestion seed or up to 100 metric
seeds, and a maximum output `limit` of 1000. Provider locales such as `GLB` or
multi-part language codes are not yet supported by that adapter.

**The display/output limit is not a billing cap.** Keyword Tool may return and
charge for more results than the plugin displays. There is no automatic retry,
alphabet expansion, recursive suggestion search or provider fallback.

## 3. Resolve known hashtags and inspect their media with Meta

Obtain a professional-account access token with the access your app needs.
Facebook Login and Instagram Login have different onboarding/permission models.
Follow the current Meta app dashboard and the linked setup documentation rather
than reusing a password or cookies. Account IDs are numeric IDs, not usernames.
The module does not implement OAuth login, token refresh or an app-review bypass.

```powershell
$env:INSTAGRAM_ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"
$env:INSTAGRAM_USER_ID = "YOUR_NUMERIC_IG_USER_ID"
$env:INSTAGRAM_GRAPH_VERSION = "v26.0"
```

`v26.0` is an example version shown in current Meta documentation, not a promise
that it is the right version for every app. Choose the version supported by your
app. There is deliberately no silently chosen API-version default. [6]

Each setting can instead be supplied with `--option access_token=...`,
`--option user_id=...`, and `--option graph_version=...`. Explicit invalid/blank
credentials fail rather than falling back to another account's environment key.
Prefer secret injection to literal command-line values: arguments and shell
history can expose credentials. Normal results and errors do not contain them;
external debuggers and application logging remain outside this guarantee.

### Check rolling hashtag usage

```powershell
keywordmoves run instagram --operation quota --option login=facebook
```

This returns the `recently_searched_hashtags` IDs/names visible for the account,
plus a partial-list flag. It does not infer a guaranteed remaining quota from a
bounded list. The limit is shared with other applications using the account. [5]

### Resolve and analyse a tag

```powershell
keywordmoves run instagram `
  --operation hashtag `
  --keyword "ukgarage" `
  --option login=facebook `
  --option edge=both `
  --option pages=2 `
  --option page_size=50 `
  --option include_keywords=true `
  --option sort_by=sampled_post_count
```

The module first calls `ig_hashtag_search` for an exact tag ID. It then reads
`top_media`, `recent_media`, or both according to `edge=top|recent|both`.
An empty ID response is `unresolved-by-api`, not “banned”. A returned ID is
`meta-resolved`. New co-occurring tags are `observed-in-sample`, not automatically
sent back for additional quota-consuming lookups.

Recent media is a bounded recent surface (the documented last 24 hours), not a
complete firehose; top media is ranked rather than random. The plugin requests
only `id,caption,media_type,permalink,like_count,comments_count` on hashtag edges.
It does not request unsupported hashtag-media usernames/timestamps or invent a
timestamp from result order. Therefore time-window counts are unavailable for
hashtag samples without actual timestamps. Source collection labels distinguish
top/recent and which seed returned each media item. [2–4]

The same media ID returned under multiple tags or top/recent is counted once.
Its collection labels are combined. Non-null conflicting counts retain the
first observation and increment a conflict counter; repeated likes are not
summed. Missing counts stay missing, and explicit zero stays zero.

### Professional account, competitor, profile and caption research

Your authorised account's media:

```powershell
keywordmoves run instagram `
  --operation account `
  --option login=instagram `
  --option include_profile=true `
  --option include_keywords=true `
  --option pages=3
```

`include_profile=true` also requests the professional profile's name and biography
and analyses their tags/words separately from media. Profile text never becomes
a fictional post. Raw biography text is not included by default.

For a public professional account available through Business Discovery:

```powershell
keywordmoves run instagram `
  --operation account `
  --option login=facebook `
  --option username=TARGET_PROFESSIONAL_USERNAME `
  --option include_profile=true `
  --option include_keywords=true
```

Business Discovery is not an arbitrary private/personal-account scraper. [7]
For your own account, `media_edge=stories` selects accessible current stories;
`media_edge=tags` selects available tagged media and requires Facebook Login.
Other-account Business Discovery is limited to `media` in this module. [8]

Available timestamps, media type/product type, profile follower count and profile
media count are retained. When using account follower counts to normalise
engagement, the result is explicitly a ratio using the available **current**
count, not necessarily the count at publication. Your follower count is never
used to normalise somebody else's post merely because it tags your account.

### Owned-media insights and comments

```powershell
keywordmoves run instagram `
  --operation media-insights `
  --option media_id=YOUR_MEDIA_ID `
  --option "metrics=views,reach,saved,shares,total_interactions"
```

Each insight is requested separately so one unsupported metric need not suppress
other available measurements. Supported requested names are `views`, `reach`,
`saved`, `shares`, `likes`, `comments`, `total_interactions`, and `replies`.
Availability depends on media type, account, age, permissions and API version.
Null, empty, unsupported and zero are not collapsed together. Authentication and
permission errors fail the operation; a Meta code-100 response to an individual
metric is labelled unavailable **for that request**, not asserted globally absent.
Raw provider error messages are not echoed. [9]

**These insights describe the whole media item.** If a post uses three tags,
its reach is not apportioned to those tags or claimed as incremental reach caused
by them. Likes/comments and `total_interactions` are separate metrics, not summed
into an inflated total.

```powershell
keywordmoves run instagram `
  --operation comments `
  --option media_id=YOUR_MEDIA_ID `
  --option include_keywords=true
```

Comments provide audience language and additional tags on authorised media.
They are text samples, not new media posts or global hashtag engagement data.
This implementation reads top-level comments, not recursively every reply. [9]

## 4. Broader hashtag, keyword and Reel discovery through Apify

Three documented Apify actors are exposed as five presets. They are third-party
scrapers with their own availability, collection bias and pricing, **not Meta
first-party analytics**. Verify your right to collect/use the data and the
provider's current terms before starting a job. [11–15]

| `actor` preset | Actor | Purpose |
| --- | --- | --- |
| `hashtag-stats` (default) | `apify/instagram-hashtag-analytics-scraper` | Provider-reported post counts, activity estimates, related tags and optional top/latest posts. |
| `hashtag-posts` | `apify/instagram-hashtag-scraper` | Posts/Reels matching supplied tags. |
| `keyword-posts` | `apify/instagram-hashtag-scraper` | Actor keyword-search mode for broader caption/topic discovery. |
| `hashtag-search` | `apify/instagram-search-scraper` | Hashtag search suggestions. |
| `reels-search` | `apify/instagram-search-scraper` | Actor `popular` search type for keyword-based Reel discovery. |

### Start explicitly, then fetch separately

```powershell
$env:APIFY_TOKEN = "YOUR_APIFY_TOKEN"

keywordmoves run instagram `
  --operation apify-start `
  --keyword "ukgarage" `
  --keyword "brightonmusic" `
  --option actor=hashtag-stats `
  --option include_posts=true `
  --option allow_paid=true `
  --option max_charge_usd=1
```

This submits one asynchronous actor run and returns its `run_id`. Submission
is not completed collection. The remote job can continue after the local command
exits. The plugin sends Apify's `maxTotalChargeUsd`, a run timeout (default 300
seconds), `waitForFinish=0`, and `restartOnError=false`. It does not poll,
retry, restart or silently launch another job. Apify enforces its server-side
budget semantics; review the current pricing before enabling paid work. [14]

Later, fetch that same run:

```powershell
keywordmoves run instagram `
  --operation apify-fetch `
  --option run_id=RETURNED_RUN_ID `
  --option dataset_kind=hashtags `
  --option "scope=ukgarage-brightonmusic:analytics:build-pinned" `
  --option sort_by=reported_post_count
```

Use `dataset_kind=media` for post/Reel datasets. Pending runs are explicitly
incomplete, failed runs error, and only successful runs are downloaded as
completed jobs. The observation time is the run's `finishedAt` (a collection
completion timestamp, not proof every record was observed at that instant).

A dataset can also be imported directly using `dataset_id` instead of `run_id`;
then `observed_at` is required. Each route requires `scope`: use the same exact
label only for genuinely comparable seed sets, actor settings, geography and
surfaces. The plugin cannot reconstruct omitted job settings from a manually
chosen scope label. `actor_build=...` is available at submission for reproducibility.

Other submission options: `results_per_seed` (default 50, maximum 250),
`results_type=posts|reels` for post/keyword presets, `actor_timeout` (1–3600),
`max_charge_usd` (>0, at most 100) and `include_posts` for analytics. A request
accepts at most 10 seeds. Counts for the actor's result limit are **per seed**, not
necessarily the total job output. Search-actor terms must not contain commas.

Fetch options: `pages` (default 1, maximum 10), `page_size` (default 100, maximum
1000), `max_records` (default 2000, maximum 10000), plus transport bounds. Paging
uses offsets with `skipEmpty=false`; empty records are not skipped and then
mistakenly counted as a shorter page. Unrecognised/error rows fail explicitly. [15]

Statistics imports preserve the actor's related/frequent/average/rare group
labels without interpreting “frequent” as a calibrated competition score.
Compact counts such as `2.5M` and `2.15 G` remain marked approximate. Native
integer `postsCount` is a provider report, not independent certification that
the global total is exact or fresh. An Apify unavailable-like sentinel of `-1`
is converted to null, not zero. Views, video views and plays remain distinct.

## 5. Browser observations and exports

### JSON and CSV

```powershell
keywordmoves run instagram `
  --operation import-media `
  --input .\instagram-posts.json `
  --option "source=Authorised Instagram export" `
  --option "scope=my-account:feed:fixed-date-window" `
  --option observed_at=2026-10-02 `
  --option include_keywords=true
```

Supported JSON shapes include a list of media objects, Graph `{"data":[...]}`,
Apify media rows, selected Instagram personal-export `title`/`media` records,
and the explicit canonical format:

```json
{
  "schema": "keywordmoves-instagram/v1",
  "posts": [
    {
      "id": "123",
      "caption": "Example #UKGarage #Brighton",
      "timestamp": "2026-10-01T12:00:00Z",
      "media_type": "VIDEO",
      "likes": 12,
      "comments_count": null,
      "views": 120,
      "reach": null
    }
  ]
}
```

Canonical media CSV uses corresponding field names. For differently named
Business Suite/provider columns, map them to these fields before import; there
is no guessing which ambiguous “engagement” column means likes or reach.
Do not insert missing measurements as zero. Available aliases include Graph
`like_count`, `comments_count` and Apify `likesCount`, `commentsCount`,
`videoViewCount`, `videoPlayCount`, `sharesCount`, `savesCount`, `followersCount`.
An exported `comments` array is not treated as a numeric comments total.

Known/observed hashtag statistics use `import-hashtags`:

```powershell
keywordmoves run instagram `
  --operation import-hashtags `
  --input .\hashtag-observations.csv `
  --option hashtag_column=Tag `
  --option post_count_column=Posts `
  --option "source=Reviewed Instagram search display" `
  --option "scope=signed-in-GB:same-account" `
  --option observed_at=2026-10-02
```

Canonical JSON uses `hashtag_stats` instead of `posts`, with entries such as
`{"name":"ukgarage","post_count":1234,"availability":"observed"}`.
Use `post_count_display` for compact UI strings and `count_is_approximate:true`
when appropriate. States are `observed`, `unavailable`, `restricted` and `unknown`.
An unavailable/restricted/unknown record cannot also assert a numeric post count.
CSV equivalents retain the availability field; explicit precision flags use
`true`/`false`. Selected **saved** native tag-search JSON shapes are supported;
that does not mean the plugin calls Instagram private search endpoints.

All observations in one import must share the declared source/capture/scope.
Separate mixed dates or methods into separate imports. `observed_at` is when the
measurement was captured, not a historical post's publication time. ISO dates
are accepted at day precision; use timezone-qualified ISO timestamps for real
within-day comparisons. Source labels and capture times supplied by the user
are recorded, not independently verified.

### Explicit saved-HTML selectors

```powershell
keywordmoves run instagram `
  --operation import-html `
  --input .\saved-search-results.html `
  --option selector=.hashtag-result `
  --option name_selector=.hashtag-name `
  --option count_selector=.post-count `
  --option "source=Reviewed rendered results" `
  --option "scope=provider-search:GB" `
  --option observed_at=2026-10-02
```

The selectors above are examples, **not verified selectors for Instagram or
any particular third-party website**. Inspect your saved DOM and choose actual
result/name/count selectors. Use browser-rendered HTML when a site uses
JavaScript. The parser does not run scripts, authenticate, load embedded assets,
or fetch arbitrary URLs from the document. No matched nodes is an input failure,
not “zero demand”. Local HTML extraction does not prove the capture is current.

### Direct public hashtag page: deliberately experimental

```powershell
keywordmoves run instagram `
  --operation public-page `
  --keyword "ukgarage" `
  --option allow_web=true
```

The fixed query route is `https://www.instagram.com/explore/tags/{encoded_tag}/`.
It checks robots policy, sends a bounded unauthenticated request, validates the
canonical hashtag URL, then looks for an English post-count display in
`og:description`. It **does not** assume that a current Instagram response will
expose that metadata. Counts from this route are conservatively approximate.

This route was contract-tested against synthetic HTML, not successfully verified
against a live current Instagram response. Login redirects, CAPTCHA/challenge
pages, robots denial, changed markup, missing counts and unsupported localisation
stop the operation. There is no cookie import, private mobile/GraphQL endpoint,
headless login, proxy rotation or bypass. Use an authorised API or a reviewed
export when public HTML is unavailable.

## 6. Measure change between comparable snapshots

Save JSON outputs as UTF-8. In Windows PowerShell 5.1, ordinary redirection can
produce UTF-16, which the UTF-8 importer rejects. Use `Out-File -Encoding utf8`:

```powershell
keywordmoves run instagram --operation import-hashtags --input .\counts-day1.json `
  --option "source=My reviewed provider" --option "scope=same-surface" `
  --option observed_at=2026-10-01 | Out-File -Encoding utf8 .\snapshot-day1.json

keywordmoves run instagram --operation import-hashtags --input .\counts-day2.json `
  --option "source=My reviewed provider" --option "scope=same-surface" `
  --option observed_at=2026-10-02 | Out-File -Encoding utf8 .\snapshot-day2.json

keywordmoves run instagram --operation compare `
  --input .\snapshot-day1.json --input .\snapshot-day2.json
```

Inputs must be oldest first and have equal `comparison_scope`, matching source,
metric, unit and geography. `metric` defaults to `reported_post_count` and can
also be `estimated_search_volume` or `sampled_post_count`.

Results include net delta, net delta per elapsed day, and percentage change.
A zero baseline gives a null percentage, not infinity. Negative changes are
retained. Approximate/rounded counts are excluded from numeric changes rather
than producing spurious growth. Missing/incompatible/conflicting measurements
are not filled in. Equal sampling settings do not remove sampling bias or make
a changing ranked sample representative of the whole platform.

`docs/examples/instagram-workflow.py` is a source-checkout demonstration using
**synthetic** fixtures. It exercises media analysis, dated snapshots and change
analysis without any network or paid service, writing results to `instagram-demo/`.

## Coverage of other possible research avenues

| Avenue | Coverage in this patch / limitation |
| --- | --- |
| Known-tag lookup, related tags and sampled popularity | Implemented through the explicit routes above; not an exhaustive inventory of Instagram. |
| Native Instagram account/keyword/tag/Reel searches | Apify keyword/search presets where the actor supports them; otherwise reviewed captures/imports. Personalisation and login scope must be recorded. |
| Owned feed, current stories, tagged media and public professional competitors | Implemented Meta account routes. No unrestricted personal/private-account collection. |
| Captions, profile names/bios and top-level comments | Local word/tag extraction; optional profile text; authorised comments endpoint. |
| Mentions, comment replies, webhooks | The generic imports accept captured text, but no webhook server, full reply traversal or mention collector is added. A mention API is not a global keyword-search API. [8–9] |
| Image alt text, on-image lettering and spoken/video captions | Import already obtained text through `extract`; no OCR, speech transcription, audio scraping or semantic image tagging pipeline is added. |
| Search engine `site:instagram.com` results | Existing external keyword/search workflows can identify candidates; imported captures are not Instagram-native search-volume evidence. |
| SISTRIX, Flick, Inflact and other browser/paid analytics tools | Reviewed CSV/JSON/saved-HTML imports. No universal working website selector or authenticated private API was verified for these tools in this patch. [16–18] |
| Keywords Everywhere and other tools showing Google volume beside Instagram ideas | Keep that volume explicitly Google-scoped; it is not substituted for Instagram demand. [19] |
| Research-only or restricted-platform datasets | Require their own eligibility, contracts and schema review; not presented as ordinary marketing API access. |
| Automated scoring, global “banned hashtag” lists or guaranteed reach forecasts | Not implemented; these would exceed what the collected evidence establishes. A generic access error is not a reliable ban detector. |

Research has finite coverage: this is a broad set of concrete routes, not a claim
that every third-party product or every Instagram surface is supported.

## Processing limits, provenance and privacy

Network credentials are resolved per invocation. Requests use fixed HTTPS hosts,
no redirects, bounded response sizes and sanitized normal errors. Default Meta/
Apify/public `max_requests` is 20 (maximum 20); each request counts, including
robots checks and pagination. Default response size is 2 MB (up to 10 MB), timeout
30 seconds (1–120). Requests are paced within the run; concurrent independent
processes do not share a rate limiter. Meta cursors are validated and pagination
URLs are never followed directly or exposed with embedded tokens.

Meta `pages` is **per edge**, default 1, maximum 10; `page_size` is at most 50;
`max_media` defaults to 500 per edge (maximum 2000). Minimum first-page hashtag
request cost is checked before querying; extra pages can still exhaust the global
budget and fail the run. Repeated cursors fail. Limits/partial collections are
reported, not silently described as complete.

Local imports default to 5 MB combined input (maximum 20 MB), 2000 records
(maximum 10000). Oversized input is rejected rather than silently truncated.
The analyser caps distinct tags per record at 100, stored source spans per record
at 5000, total tag assignments at 50000, and vocabulary/statistics processing at
roughly 20000 candidates. Split large datasets into comparable batches. Text
extraction supports files and inline text, not automatic recursive directories.

`include_media=true` opts into storing canonical captions/media metrics in output.
Default output stores aggregate statistics, limited hashtag offsets and source
labels, not a dump of usernames, contact fields, raw API bodies or credentials.
Even aggregate output can contain sensitive topics; retain and share it only as
appropriate for your authorised use. The plugin makes no posts or account changes.

## Tests and verification

```powershell
python -m pip install -e ".[dev,online]"
python -m pytest tests/test_instagram_analysis.py tests/test_instagram_api.py tests/test_instagram_imports.py
python docs/examples/instagram-workflow.py
```

The new CI workflow runs these contracts and linting on Linux/Windows and Python
3.10–3.13. All API responses, HTML fixtures and example counts used in these tests
are synthetic. Tests exercise actual HTTPX serialization and HTML parsing, not
live account entitlements or provider availability. There are no CI credentials,
paid jobs, automatic API smoke requests or pretrained model requirements.

Run a small authorised live smoke check separately before relying on production
results. Verify the selected app version, permissions, field availability, token
scope, actor build, returned counts/units and billing. Failure never triggers an
unannounced second provider.

## Sources and contract references

Accessed/researched 2026-10-02. Some Meta pages rate-limited full page retrieval;
relevant facts were checked in indexed official documentation. No private account
data or live authenticated API response was used to establish these contracts.

1. Meta, Instagram Platform overview and login capability comparison:
   https://developers.facebook.com/documentation/instagram-platform/overview
2. Meta, Hashtag Search:
   https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/hashtag-search
3. Meta, IG Hashtag and exact search reference:
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag-search
4. Meta, hashtag media edges:
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag/top-media
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-hashtag/recent-media
5. Meta, recently searched hashtags:
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/recently_searched_hashtags
6. Meta, current Instagram Login /me example:
   https://developers.facebook.com/documentation/instagram-platform/reference/me
7. Meta, Business Discovery:
   https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/business-discovery
8. Meta, tagged and mentioned media:
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/tags
   https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/mentioned_media
9. Meta, media, insights and comments:
   https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media
   https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights
   https://developers.facebook.com/documentation/instagram-platform/comment-moderation
10. Keyword Tool API documentation and Instagram page:
    https://keywordtool.io/api
    https://docs.keywordtool.io/reference/search-volume-instagram
    https://keywordtool.io/instagram
11. Apify, hashtag analytics and input schema:
    https://apify.com/apify/instagram-hashtag-analytics-scraper
    https://apify.com/apify/instagram-hashtag-analytics-scraper/input-schema
12. Apify, hashtag/keyword post actor input:
    https://apify.com/apify/instagram-hashtag-scraper/input-schema
13. Apify, search actor input:
    https://apify.com/apify/instagram-search-scraper/input-schema
14. Apify, asynchronous actor run API:
    https://docs.apify.com/api/v2/actors-runs-post
    https://docs.apify.com/api/v2
15. Apify, dataset pagination:
    https://docs.apify.com/api/v2/dataset-items-get
16. SISTRIX Instagram hashtag interface:
    https://app.sistrix.com/en/instagram-hashtags
17. Flick, tracking limitations:
    https://help.flick.tech/en/articles/4528829-what-type-of-posts-can-flick-track
18. Inflact hashtag research interface:
    https://inflact.com/hashtag-generator/
19. Keywords Everywhere's Instagram keyword generator explicitly identifies its
    accompanying demand values as Google search volume:
    https://keywordseverywhere.com/tools/instagram-keyword-generator/
