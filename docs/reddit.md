# Reddit keyword, community and conversation research

Research date: **2 October 2026**. Patch baseline:
`f10d2811f7b704cbcf90382d6ff997bdf4a10475`.

The `reddit` keyword plugin adds 27 read/analysis operations. It does not change
any other provider, replace the Google Search module, or introduce an LLM
runtime. Source text is analysed locally with a transparent Unicode tokenizer.
No author profiling, posting, voting, messages, model training or automatic
cross-provider fallback is implemented.

## Access status: read this before configuring a live source

**The native adapter targets approved, legacy OAuth Data API access, not a
permanent unrestricted Reddit API.** Reddit's current policy requires explicit
approval; commercial uses need written approval. Its published transition
schedule stops new public-API requests on **31 October 2026**, begins removing
unregistered/unengaged access on **12 January 2027**, and closes remaining public
access in **March 2027**. See primary sources [1–3]. Existing credentials do not
prove that this use is authorised or will remain available.

The client accepts an externally obtained access token and truthful app/contact
User-Agent. It does not register an app, refresh a token, install a Devvit app or
negotiate a data licence. The `api_access_approved=true` option is your declaration
of prior approval, not an approval mechanism. Imports and the analysis layer are
independent of the native transport so approved replacement sources can supply
canonical records without changing the keyword evidence model.

The native transport follows [1]: it captures Reddit's rate-limit headers and
stops when the remaining budget is exhausted. Reddit documents 100 queries per
minute per eligible free-access OAuth client, averaged over a ten-minute window.
This plugin uses a smaller per-run request bound and at least one second between
calls; **separate processes sharing a client still need coordinated limits**.
No unauthenticated `.json`, RSS, old.reddit session or challenge-bypass fallback
is offered. A failed response is an access/availability error, not zero demand.

## Installation and registration

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

The existing `online` extra supplies HTTPX and Beautiful Soup. Listing plugins,
analysing reference text, and JSON/CSV processing need neither an API key nor
heavy NLP libraries. HTML imports need Beautiful Soup; live operations need
HTTPX. No optional library is imported just to describe the plugin.

This is a keyword plugin named `reddit`. There is no new `--model` or `--llm`
selection. Generative model options are rejected rather than silently sending
Reddit data to another service.

## Operation inventory

### Approved native OAuth reads

| Operation | Purpose | Required arguments beyond credentials |
| --- | --- | --- |
| `search` | Search posts and analyse returned titles/body/flair. | One `--keyword`; optional `subreddit`, `sort`, `time`. |
| `hashtag` | Search for a literal `#word`, then verify it in returned source text. | One `--keyword`, with or without `#`. |
| `subreddit-feed` | Analyse a bounded new/hot/top/rising/controversial listing. | `subreddit`. |
| `posts` | Refresh specified post objects in one `/api/info` request. | Up to 100 repeated `--keyword` post IDs/fullnames. |
| `refresh` | Re-read known post/comment fullnames, excluding stale missing objects. | Up to 100 repeated `--keyword t3_…` / `t1_…`. |
| `comments` | Analyse returned comment/reply trees for one post. | `post_id`; optional `comment_sort`, `depth`, `page_size`. |
| `more-comments` | Explicitly expand an omitted comment stub. | `post_id`, comma-separated `comment_ids` without `t1_`. |
| `communities` | Search community names/descriptions and return available community counters. | One `--keyword`. |
| `community-autocomplete` | Resolve native community-name suggestions, excluding user profiles. | One `--keyword`, at most 25 characters. |
| `community` | Retrieve a specified community's public details. | `subreddit`. |
| `community-rules` | Read rule names/descriptions as participation context. | `subreddit`. |
| `duplicates` | Read one bounded duplicate-submission response for a post. | `post_id`. |
| `link-posts` | Find submissions sharing a supplied public URL. | One `--keyword` HTTP(S) URL without query/fragment. |

### Providers and analysis

| Operation | Purpose | Route |
| --- | --- | --- |
| `suggestions` | Keyword Tool Reddit phrase or community suggestions; optional metrics. | Explicit paid-provider API. |
| `metrics` | Keyword Tool Reddit estimated volume for up to 100 supplied phrases. | Explicit paid-provider API. |
| `web-search` | Google-indexed Reddit pages through SerpApi. | Google result positions, not native Reddit ranks. |
| `apify-start` | Submit one approved/paid-capable collection using one of four presets. | Asynchronous Apify job. |
| `apify-fetch` | Read the same successfully completed supported-actor run. | Bounded dataset page, no resubmission. |
| `competition` | Inspect discussion/attention context for one literal phrase. | Native search, or supplied post file with `--input`. |
| `import-posts` | Analyse canonical records, native Things/Listings, saved record-bearing results or explicitly mapped CSV. | Local. |
| `import-comments` | The corresponding comment-text analysis. | Local. |
| `import-communities` | Import native or canonical community objects from JSON. | Local. |
| `import-observations` | Preserve reviewed source-reported measures using a versioned schema. | Local JSON. |
| `import-html` | Parse a saved rendered result page with explicit selectors. | Local HTML, not live browser automation. |
| `analyse-text` | Discover words, phrases, literal hashtags and community mentions in a supplied reference. | Inline text or one UTF-8 file. |
| `compare` | Compare compatible saved outputs, oldest first. | Two JSON results. |
| `redact` | Remove specified IDs from saved records and rebuild derived keywords. | Local result with `include_records=true`. |

These are implemented routes, not promises that an account is entitled to each
endpoint. Native request/response shapes follow [4]; they are tested offline, not
against an authenticated production account.

## Native setup and examples

Use your approved application's credentials, not a browser cookie:

```powershell
$env:REDDIT_ACCESS_TOKEN = "YOUR_APPROVED_OAUTH_ACCESS_TOKEN"
$env:REDDIT_USER_AGENT = "script:keywordmoves:0.2.0 (by /u/YOUR_REDDIT_USERNAME)"

keywordmoves run reddit `
  --operation search `
  --keyword "uk garage" `
  --option subreddit=Music `
  --option sort=new `
  --option time=month `
  --option pages=2 `
  --option api_access_approved=true `
  --option limit=100
```

Explicit `access_token` and `user_agent` options override the environment. Blank
or invalid explicit values do not fall back to a different account's settings.
Environment-based secret injection is preferable to command-line secrets,
which may be visible in history or process listings. Raw error bodies, tokens
and request headers are not copied to result metadata. External HTTP debug
logging/process inspection are outside that guarantee.

The API request uses `type=link`, `raw_json=1`, and `restrict_sr` where a community
is selected. An unqualified query follows Reddit's search syntax. Set
`literal=true` to quote an ordinary phrase. Supported manual query operators can
be supplied directly, for example `flair:"Discussion" title:music` [5]. Search
results are not guaranteed to contain the whole phrase. The `hashtag` and
`competition` operations perform their own subsequent literal-match check.

For a known hashtag:

```powershell
keywordmoves run reddit --operation hashtag --keyword "#UKGarage" `
  --option subreddit=Music --option api_access_approved=true
```

This does **not** manufacture a native hashtag page, hashtag total or official
hashtag-volume metric. It records matching source text and co-occurring language.
`# Heading` is Markdown rather than a literal hashtag. Ordinary `#UKGarage`, post
flair, plain keywords and `r/Music` mentions are distinct candidate types.

To discover communities, use `communities` or `community-autocomplete`. To inspect
a community's rules before participating, use:

```powershell
keywordmoves run reddit --operation community-rules `
  --option subreddit=Music --option api_access_approved=true
```

Rules have no demand score, and the module never posts. Community subscriber and
available active-account counters are capture-time observations, not monthly
search volume. Native collection has no country/language targeting in this
adapter; those options are rejected rather than falsely attaching a geography.

### Pagination and comments

Listing-based routes support `pages=1..10` and `page_size=1..100` (defaults 1 and
50). They return `next_after`, `listing_exhausted`, and `collection_complete=false`.
The last flag remains false even at the end of a returned listing: this is not
proof of a complete historical inventory. `listing_positions` records order
within this request's returned rows, not an absolute/population rank. Repeated
cursors fail immediately. A run never follows a response-supplied external URL.

To resume, pass the returned fullname using `--option after=t3_…`. Defaults and
query/sort/time parameters form the saved collection scope. Different collection
settings produce different scopes. Do not compare samples from differently
entitled accounts merely because their visible settings match.

```powershell
keywordmoves run reddit --operation comments `
  --option post_id=t3_ABC_REPLACE_WITH_REAL_ID `
  --option comment_sort=top --option page_size=100 --option depth=8 `
  --option api_access_approved=true
```

Replace the placeholder with an actual lowercase base36 ID, for example `t3_abc123`.
`comments` permits up to 500 requested comments and depth 20. It reports omitted
IDs as `more_comment_ids`; `comment_tree_complete` is deliberately false. Expansion
is a separate request, never an unbounded recursive crawler:

```powershell
keywordmoves run reddit --operation more-comments `
  --option post_id=t3_abc123 --option "comment_ids=def456,ghi789" `
  --option api_access_approved=true
```

Up to 100 child IDs are accepted per expansion, including longer comment IDs.
No global comment-search method is invented for the native Data API. Supplied
comment trees, authorised imports and the explicitly separate third-party
comment-search preset cover other routes.

## What the analyser measures

Text candidates use literal Unicode word sequences of one to three words by
default. Their occurrence and record counts are distinct. Stopword filtering
does not join formerly separated words: `paper and planes` does not become
`paper planes`. Phrases do not cross sentence/line/title/body boundaries. Code
blocks, inline code, quoted lines, link destinations and user mentions are
excluded from the plain-language stream; this is a pragmatic Markdown filter,
not a full CommonMark renderer. Hashtags and community mentions remain separate
categories. Post flair is included as an entire label, not guessed user flair.

A shared post/comment fullname is deduplicated. Conflicting snapshots are flagged
and the first survives; a deletion/removal marker overrides both earlier and
later copies of the same ID. Different crossposts remain different submissions,
with their `crosspost_parent` preserved when available.

Each candidate can carry:

- Literal occurrence count, document frequency, title/body/flair counts, source
  record IDs, and the distribution of available subreddit labels.
- Separate post and comment record counts, score means/medians and availability
  denominators. Post comment-count and upvote-ratio means/medians have their own
  denominators. Ambiguous third-party vote fields stay `provider_reported_votes`.
- Counts of timestamped matching sample records within 7 and 30 days of capture.
  Missing or future dates never fabricate recent activity. These are sample
  counts, not a platform-wide posting rate.
- A bounded co-occurrence table for top selected candidates: shared-record count
  and Jaccard overlap. Repeated words in one record do not inflate its overlap.

Negative reported scores are valid. Hidden scores become `null` even when a
payload contains a number. Zero remains zero. The plugin never reconstructs
exact up/down vote counts from a net score and ratio, never relabels comments as
impressions, and never claims that a keyword caused its containing post's score.
`KeywordCandidate.score` remains unset throughout the plugin.

`competition` uses one ordinary search sample or a supplied post file. It reports
sample size, literal matches, subreddit/linked-host distribution, matching-score
and comment medians with denominators, and locked/stickied post counts. These
measure discussion context; they are **not SEO difficulty, a posting strategy,
an audience-size estimate or a forecast of ranking success**. Use the separate
`google-search` module for Google-ranking competition on Reddit URLs.

### Analysis options

| Option | Default | Meaning/bound |
| --- | --- | --- |
| `limit` | 50 | Returned candidates, 1–1,000; truncation is reported. |
| `min_words`, `max_words` | 1, 3 | Literal n-gram lengths, 1–5. |
| `min_occurrences` | 1 | Minimum occurrence count, 1–10,000. |
| `stopwords` | Empty | Comma-separated additions to the documented small English function-word list in source. |
| `include_keywords`, `include_hashtags`, `include_flair` | true | Independent candidate controls. |
| `include_nsfw` | false | Do not analyse flagged adult records unless enabled. Does not alter account access/preferences. |
| `exclude_stickied` | false | Optionally omit pinned posts. |
| `include_records` | false | Include normalized source records for short-lived refresh/redaction workflows. |
| `max_records` | 1,000 | At most 10,000 source rows; duplicates still count toward input bounds. |
| `max_text_chars` | 2,000,000 | Post title/body input cap, at most 10,000,000. |
| `max_candidates` | 10,000 | Vocabulary cap, at most 50,000; overflow fails rather than silently truncating input. |
| `cooccurrence_limit` | 30 | At most 100 selected candidates; ten peers per candidate. Zero disables the association matrix. |
| `sort_by` | `document_frequency` | Also `phrase`, `sample_post_score_median`, `sample_post_comments_median`, `sample_comment_score_median`, `sample_posts_7d`. |

Lexical rules are language-agnostic at the Unicode token level, but the built-in
stopwords are English, and this does not provide language detection, stemming,
POS tagging, semantic clustering or sentiment analysis. The existing local
extractors remain available separately for material you are permitted to process.

## Provider suggestions and estimated demand

Keyword Tool documents Reddit phrase and community suggestions, plus volume
requests [6]. These are provider-sourced results, not a first-party Reddit
keyword-planning feed. The adapter excludes its profile-suggestion type.

```powershell
$env:KEYWORDTOOL_API_KEY = "YOUR_KEY"
keywordmoves run reddit --operation suggestions --keyword "uk garage" `
  --option country=GB --option language=en --option metrics=true `
  --option sort_by=estimated_search_volume
```

Use `suggestion_type=communities` for community candidates. `metrics` accepts up
to 100 repeated `--keyword` arguments; suggestions accepts one. The provider
limits each phrase to 80 characters/10 words. `country` is required, uppercase
ISO country or `GLB`; language can include a supported variant such as `en-GB`.
The provider decides which combinations it supports.

`currency=USD` is the default for CPC; `sandbox=true` uses its documented sandbox
base. Monthly `m…` values are retained under their native field names. A provider
notice sets `partial_notice` rather than promising complete results. Query,
country, language, currency, endpoint and sandbox status are retained in the
credential-free scope.

Estimated volume, returned-order position, Google Ads-derived CPC proxy and paid
competition proxy remain separate. A phrase suggestion is not a verified hashtag.
Use the native literal lookup or your observed source text to verify actual use.
For these provider results, `sort_by` names a returned numeric evidence metric;
ordinal position sorts ascending, volume sorts descending, and missing values
sort last. The display limit is **not** a universal billing cap.

`web-search` uses SerpApi Google `site:reddit.com` results. Its `country=gb` and
`language=en` options control Google context, not Reddit demographics. Only
returned Reddit-host URLs are retained. It neither follows those pages nor
claims native Reddit rank/volume. Titles/snippets can be stale or truncated.

## Explicit third-party collection through Apify

Four presets use the documented `trudax/reddit-scraper-lite` actor [7]:
`search-posts`, `search-comments`, `search-communities`, and `subreddit-posts`.
This is not a licensed-data guarantee, a substitute for Reddit approval, or a
fallback automatically invoked after an API denial. Check your and the
provider's permitted collection/use before authorising a job.

```powershell
$env:APIFY_TOKEN = "YOUR_TOKEN"
keywordmoves run reddit --operation apify-start --keyword "uk garage" `
  --option actor=search-posts --option subreddit=Music `
  --option allow_paid=true --option collection_authorized=true `
  --option max_charge_usd=1 --option results_per_seed=30
```

The two approval flags must be explicit. `max_charge_usd` is positive and at
most 100; the request passes it as `maxTotalChargeUsd` with a 300-second job
limit [8]. Provider pricing/enforcement still govern actual spend. The output
contains a run ID, status and the exact credential-free collection scope.
Submission is not completion. The module does not retry, poll, restart or submit
another job on fetch. The actor itself controls its own internal requests and
retry/proxy behaviour.

**Despite the option name, `results_per_seed` is applied conservatively as a
run-wide `maxItems` and `maxPostCount` ceiling**, not multiplied by the number of
seeds. It defaults to 30 and is bounded to 500. Up to five seeds are allowed.
Use separate jobs for independently bounded samples. Comment records can consume
the same item ceiling, and the provider may stop before that ceiling.

Comments are opt-in with `include_comments=true` for post jobs and enabled for
the comment-search preset. `comment_limit=20` controls its `maxComments` cap.
User/profile and media searches are disabled. Detailed metadata extraction is
enabled to request reported votes and comment counts; returned media links and
author fields are not retained by this plugin. The actor's sort/time inputs
apply to search; the subreddit-URL preset rejects those options because its
upstream URL route ignores them.

After the job has completed, fetch the same run and returned scope:

```powershell
keywordmoves run reddit --operation apify-fetch `
  --option run_id=RETURNED_RUN_ID --option dataset_kind=posts `
  --option scope=RETURNED_SCOPE --option max_records=1000
```

`dataset_kind` is `posts`, `comments` or `communities`. Fetch verifies the actor
identity and requires `SUCCEEDED`, then reads one bounded dataset page. `dataset_offset`
resumes explicitly; `next_dataset_offset` is only a possible continuation, not
proof of another row. Other data types and ads are counted as skipped. Capture
uses the run's completion time, with its start time retained: this is a
collection interval, not perfectly simultaneous measurements.

## Imports, comparison, and data lifecycle

Use only your own material, permitted exports or data you are authorised to use.
Native JSON Things/Listings and canonical arrays are accepted. Each post/comment
needs a valid `t3_…`/`t1_…` identity and `kind`; canonical fields are documented in
`reddit_analysis.normalise`. Posts use `title` plus `body` (or native `selftext`),
comments use `body`. Optional fields include `score`, `num_comments`, `upvote_ratio`,
`created_at`, `subreddit`, `flair`, parent/link/crosspost IDs and flags.

```powershell
keywordmoves run reddit --operation import-posts --input .\approved-posts.json `
  --option "source=Approved export" --option "scope=fixed selection settings" `
  --option observed_at=2026-10-02 --option sort_by=sample_post_comments_median
```

Date-only capture values mean midnight UTC. Explicit timestamps must include a
timezone. Epoch creation timestamps are accepted. Files must be UTF-8 (BOM
accepted), with a 10 MB read bound. Native operations stamp their own capture time
and reject manual source/date overrides.

CSV imports require explicit `id_column` and `title_column` or `body_column`.
Optional mappings are `subreddit_column`, `score_column`, `num_comments_column`,
`upvote_ratio_column`, `created_at_column`, `flair_column`, `parent_id_column`,
`link_id_column`, `permalink_column`. `delimiter` is one non-newline character.
No locale guessing or automatic `1.2k` expansion occurs.

Saved HTML imports require `row_selector`, `id_selector`, and `title_selector`
or `body_selector`. Optional selectors cover subreddit, score, comments and
creation date. Selectors extract element text, not attributes. A reviewed
`empty_selector` can acknowledge a genuine no-results page; otherwise no rows
means an error. No selector is presented as universally matching current Reddit
markup, and no live browser session is created.

### Explicit source observations

`import-observations` accepts this schema (all values below are **synthetic**):

```json
{
  "schema": "keywordmoves-reddit-observations/v1",
  "source": "Synthetic approved metrics export",
  "scope": "same-keyword-definition-and-population",
  "observed_at": "2026-10-02T12:00:00Z",
  "country": null,
  "period": "7d",
  "observations": [
    {
      "phrase": "paper planes",
      "kind": "keyword",
      "metrics": {"reported_mentions": 42},
      "availability": "observed",
      "approximate": false
    }
  ]
}
```

Supported metric names: `estimated_search_volume`, `reported_conversation_volume`,
`reported_mentions`, `reported_views`, `reported_impressions`, `reported_subscribers`,
`reported_net_score`, `reported_comments`, `reported_shares`, `suggestion_position`,
`reported_paid_competition`. Their fixed units are listed in `reddit_imports.OBS_METRICS`.
Use `period` and `scope` to identify windows and definitions. Approximate display
strings require `approximate=true` and are excluded from numeric comparisons.
`availability=unavailable` requires null/empty measurements, not a fabricated zero.
The schema does not validate your right to export a source or make estimates
first-party data.

### Refresh and remove source data

Reddit's guidance requires deletion of removed content and author-identifying
fields for deleted accounts, and recommends routinely expiring stored user data
within 48 hours [1]. Author IDs/names/avatars/user flair are not retained here,
but text, candidate phrases and linked records can still be subject to deletion.
This implementation is **not** an automatic retention/deletion service.

`include_records=false` avoids returning full records by default. Enabling it
permits short-lived `refresh`/`redact` workflows but increases stored content.
`refresh` returns only objects fetched now; `not_returned_ids` means unavailable,
not proof of deletion. Replace old snapshots and regenerate derivatives rather
than merging stale missing records back in. `redact` takes a saved record-bearing
result and repeated fullnames, removes those records and recalculates its
keywords with the saved analysis options (explicit current options can override
them). It **does not overwrite or delete the original file/backups**. Your
application must delete them, and apply removal to every derived output.

`compare` takes two saved Reddit results, oldest first. Source, scope, country,
period and analysis signature must match; truncated output is rejected. It emits
net change, change per elapsed day and percentage change only for positive
baselines. Missing observations stay unknown and rounded displays are excluded.
Net score change is not a count of new votes. Retention/deletion restrictions
still apply to historical comparisons; this feature does not authorise long-term
storage of Reddit content.

## Further avenues: researched, not silently implied as integrations

**Reddit Pro Trends** exposes monitored topics, related keywords and, for Smart
keywords, conversation volume. Its current documentation also restricts downloads:
organic metrics for your own content have an export route, but other redditors'
posts/comments and other Pro information must not be downloaded without separate
permission. Thus this patch does **not** scrape Pro or instruct you to export its
restricted screens. Use the native interface, or import only explicitly permitted
own-content exports/licensed data [9].

**Reddit's website comment/flair search and AI search** offer additional native
research surfaces [5]. No unsupported public API is invented for AI search or
whole-site comment search. **Devvit**, licensed commercial access and the Reddit
for Researchers programme are separate approval/deployment paths, not permissions
this Python CLI can grant [2–3]. A Devvit migration is not implemented in this patch.

Third-party social-listening tools can have their own licensed sources and
proprietary share-of-voice/sentiment measures. No blanket access assumption or
score conversion is made. Approved exports can use the explicit observation
schema; unreviewed providers and any private/authenticated interface are not
claimed as live integrations. Reddit Ads reporting/targeting, mod queues,
subscriber demographics, private communities, user histories, private messages,
image OCR, audio transcription and user-level sensitive-trait inference are
outside this module.

## Testing and a reproducible offline demonstration

```powershell
python -m pip install -e ".[dev,online]"
python -m pytest tests/test_reddit_analysis.py tests/test_reddit_imports.py tests/test_reddit_api.py tests/test_reddit_providers.py
python .\docs\examples\reddit-workflow.py
```

The example writes into a new `reddit-demo` directory and refuses to overwrite it.
All example records/measurements are synthetic. The tests exercise actual HTTPX
serialization against mock transports and actual HTML parsing. They make no
paid, native or unauthenticated network calls. CI runs the new tests on Linux and
Windows across Python 3.10–3.13. Consult the patch bundle's validation report for
what was actually run locally; configured CI is not a completed CI result.

## Primary references checked

1. Reddit Data API Wiki (OAuth, rate headers and retention):
   <https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki>
2. Reddit Responsible Builder Policy (approval and distinct use cases):
   <https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy>
3. Reddit developer announcement, migration dates:
   <https://www.reddit.com/r/redditdev/comments/1wubcvf/moving_data_api_apps_to_the_developer_platform/>
4. Reddit endpoint and listing documentation:
   <https://www.reddit.com/dev/api/>
5. Reddit search features and operators:
   <https://support.reddithelp.com/hc/en-us/articles/19696541895316-Available-search-features>
6. Keyword Tool provider API reference:
   <https://keywordtool.io/api>
7. Apify Reddit actor and input schema:
   <https://apify.com/trudax/reddit-scraper-lite>
   <https://apify.com/trudax/reddit-scraper-lite/input-schema>
8. Apify run submission:
   <https://docs.apify.com/api/v2/actors-runs-post>
9. Reddit Pro Trends, capabilities and download restrictions:
   <https://support.reddithelp.com/hc/en-us/articles/47619216411284-Reddit-Pro-Feature-Trends>
10. Reddit Data API Terms:
   <https://redditinc.com/policies/data-api-terms>
