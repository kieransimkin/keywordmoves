# Keyword demand monitoring

KeywordMoves 0.4 adds private watchlists, persistent evidence history, coverage
reports and source-compatible change alerts. It records what each source
actually reported rather than inventing a universal demand score.

A Google estimate, website impression, YouTube referral, native interest index,
hashtag post count and caption phrase answer different questions.

## Quick start without accounts or network access

Use Python 3.10–3.13 with standard CPython SQLite support. File imports,
coverage, history, alerts and HTML reports need no network or NLP extra.

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite init
keywordmoves monitor --store .\private\evidence.sqlite watch --input .\watches.json
keywordmoves monitor --store .\private\evidence.sqlite import --input .\observations.json
keywordmoves monitor --store .\private\evidence.sqlite coverage
keywordmoves monitor --store .\private\evidence.sqlite alerts
keywordmoves monitor --store .\private\evidence.sqlite report --output .\private\coverage.html
~~~

The source distribution includes a reproducible synthetic example:

~~~powershell
python examples/monitor_demo.py --output-dir ./demo-private
~~~

It contains missing coverage, a censored estimate, language-only evidence,
compatible performance periods, unavailable referrals and uncertain completeness.
It makes no network requests or campaign-register changes.

## Watchlist

~~~json
{
  "schema": "keywordmoves-watchlist/v1",
  "watches": [{
    "id": "example-google",
    "subject": "Example Song",
    "platform": "Google",
    "phrase": "example song",
    "source": "Google Search Console",
    "metric": "impressions",
    "unit": "count",
    "geography": "gbr",
    "scope": "https://example.com/song/",
    "window": "28 days",
    "evidence_kind": "property_performance",
    "dimensions": {"device": "mobile"},
    "cadence_hours": 168,
    "freshness_days": 35,
    "min_absolute_change": 5,
    "min_percent_change": 20,
    "active": true
  }]
}
~~~

Required fields are **id**, **subject** and **platform**. Omitting phrase, source,
metric, unit, geography, scope, window or evidence kind makes that watch broad.
It never sums sources; it reports evidence types and the last demand-labelled
observation separately from its latest example.

Cadence defaults to 168 hours, freshness to 30 days. Thresholds use actual source
units and an optional percentage. Both must pass when both are specified.
A zero baseline has no defined percentage and can trigger an absolute-only
alert. Setting **active: false** retires a watch without deleting history.
Merging a manifest never deletes omitted watches.

## Observations

~~~json
{
  "schema": "keywordmoves-monitor-observations/v1",
  "observations": [{
    "subject": "Example Song",
    "platform": "Google",
    "phrase": "example song",
    "source": "Google Search Console",
    "metric": "impressions",
    "unit": "count",
    "geography": "gbr",
    "scope": "https://example.com/song/",
    "window": "28 days",
    "evidence_kind": "property_performance",
    "dimensions": {"device": "mobile"},
    "period_start": "2025-09-01",
    "period_end": "2025-09-28",
    "observed_at": "2025-10-01T12:00:00Z",
    "value": 12,
    "availability": "observed",
    "approximate": false,
    "censored": false,
    "completeness": "complete"
  }]
}
~~~

| Evidence kind | Meaning |
|---|---|
| search_volume_estimate | Declared provider estimates; approximate by default. |
| relative_interest | Source-normalised interest, including Google Trends. |
| platform_search_interest | Actual native search interest with its source unit. |
| property_performance | Visibility or traffic for an owned website/property. |
| channel_referral | Attributed search traffic for an authorised channel. |
| content_supply | Posts, videos or discussions, rather than searches. |
| sampled_engagement | Engagement of a declared content sample. |
| language_suggestion | Suggestions, result wording or caption language. |
| text_salience | Frequency/relevance in supplied local text. |
| proposal | A proposed phrase without established external demand. |
| unknown | Semantics have not yet been reviewed. |

Dates require ISO dates or timezone-aware ISO timestamps. Date-only captures
have day precision. Conflicting values at the same identity/time are reported
as conflicts. Reporting periods retain date-only boundaries and source timezone
in **dimensions**. Future captures and reports ending after capture are rejected.

Availability is **observed**, **unavailable**, **suppressed**, **no-data** or
**error**. Unavailable rows require a null value. Reported numeric zero stays
zero; displays such as less-than ranges and Breakout remain strings.

Completeness is **complete**, **partial** or **unknown**. It describes the retained
measurement for its scope, not proof that a platform discloses every search or
that a sample represents its population. An importer cannot attest to this.

## Existing evidence and native exports

The monitor accepts **keywordmoves-observations/v1**, the monitoring schema and
saved PluginResult JSON. Supply missing context explicitly:

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite import --input .\youtube-search-result.json --subject "Example Song" --platform YouTube --scope "Authorised channel, named video" --window "28 days" --evidence-kind channel_referral --period-start 2025-09-01 --period-end 2025-09-28
~~~

Row-labelled platforms, sources, metrics, units and geography remain
authoritative. Page/video/account IDs, language, device, category and analysis
signature remain separate dimensions. Candidate scores are not demand evidence.

Use [native-export](native-export.md) for an authorised CSV with actual column
mappings. It handles permitted reports from Pinterest, Facebook, X, LinkedIn,
Yahoo, DuckDuckGo and other sources without inventing account APIs. Prefer the
existing named module when its source-specific importer is available.

## History and provenance

Each file is fully validated before insertion; malformed input is atomic.
Identical bytes with identical import context are idempotent. The private store
retains original JSON bytes, SHA-256, source path and normalised records.
CSV-derived results retain the CSV hash; keep the original CSV separately.

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite history --watch-id example-google
keywordmoves monitor --store .\private\evidence.sqlite verify
~~~

Hashes establish byte identity, not capture accuracy. The database, exports and
reports are private and unencrypted; they can contain account data. Credential
fields are rejected. Source licences and retention rules still apply.

## Coverage and alerts

Coverage distinguishes missing, unavailable, suppressed, partial, stale, failed,
running and conflicting states. Last observed data remain visible after failure.
Capture age and reporting-period age are separate: downloading an old report
does not make its reporting period fresh.

Comparisons require the same subject, platform, phrase, source, metric, unit,
geography, scope, window, evidence kind, dimensions and normalisation identity.
They exclude missing, qualitative, approximate, censored, partial and unknown
completeness measurements. Website/channel periods must be equal-length and
non-overlapping. For rolling captures, the most recent compatible non-overlapping
earlier period is selected when available.

Relative indices require a reviewed shared **normalization_id**. Matching region
and window alone does not establish shared scaling between Trends exports.
Top indices, Rising percentages and Breakout labels remain separate.

Alerts are local JSON; no messages or notifications are sent. Deterministic
alert IDs support acknowledgement:

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite alerts --output .\private\alerts.json
keywordmoves monitor --store .\private\evidence.sqlite acknowledge --alert-id <actual-id>
~~~

The HTML report escapes imported text and loads no external scripts or fonts.
Publishing a report is a separate privacy decision.

## Due plans and collection

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite due --limit 5 --output .\private\plan.json
~~~

Watches without collectors form a manual-export/observation queue. Collectors
compose existing documented first-party readers only:

- Google Search Console: gsc-query, gsc-pages, gsc-query-pages;
- YouTube Analytics: analytics-search, analytics-hashtags;
- Bing Webmaster: bwt-queries, bwt-query-pages, bwt-keyword, bwt-related,
  bwt-keyword-history.

Live watches need exact phrase, source, metric, unit, geography, scope, window
and evidence kind. Use actual property/video/query filters; the runner does not
invent song-to-page or song-to-video associations.

A **collector** has **plugin**, **operation** and **options**. Date options may
use {period_start} / {period_end}. Dimensions **period_days** and
**settle_lag_days** default to 28 and 3. Review settled dates and native timezone;
defaults do not prove data are settled. Fixed dates remain fixed.

After reviewing the exact plan, account access, scope and finite bounds:

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite collect --plan .\private\plan.json --plan-sha256 <exact-reviewed-file-sha256> --allow-network --account google-owned-project --max-runs 5 --max-requests-per-run 3 --daily-runs 10 --daily-requests 30
~~~

Collection needs the online extra and legitimately authorised credentials from
the environment. It does not authorise OAuth setup, app review, access requests
or platform terms. Account eligibility needs an authorised smoke test.

Use the [authenticated-access guide](authenticated-access.md) before configuring
live readers. YouTube Analytics requires both `youtube.readonly` and
`yt-analytics.readonly`; Search Console uses its separate `webmasters.readonly`
scope. Browser sign-in, connector access and local API authorization are
independent states. Credentials never belong in the evidence store or a plan.

Paid providers, remote jobs, experimental browser endpoints and unsupported APIs
are excluded. Use their separately approved operations or file imports.

The shared SQLite ledger reserves worst-case requests before each run.
Reservations remain consumed after failure/interruption. A reviewed plan entry
cannot run again. Running watches block concurrent collection; explicit recovery
never refunds quota:

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite recover --attempt-id <actual-id>
keywordmoves monitor --store .\private\evidence.sqlite health
~~~

Limits use UTC days and cover only clients sharing the store/account label.
They are not provider-wide quotas or billing caps. No automatic retry, provider
fallback or expansive seed fan-out occurs.

## Scheduling and retention

Use an explicitly configured host scheduler or authorised automation for
reviewed plans. Installation, plugin listing, imports and reports never start
a background process or scheduled job.

~~~powershell
keywordmoves monitor --store .\private\evidence.sqlite prune --before 2025-09-01 --platform YouTube
keywordmoves monitor --store .\private\evidence.sqlite prune --before 2025-09-01 --platform YouTube --confirm
~~~

The first command previews. Confirmation deletes matching observations and raw
bytes from affected mixed-source snapshots, preserving unrelated normalised
observations. Empty snapshots are removed; pruned raw hashes are no longer
checked. Original exports, backups and external copies need separate retention
handling. This is not secure deletion from every device.

Tests cover persistence, source compatibility, conflicts, stale periods,
censoring, plan gates, quotas, recovery, integrity and retention. Synthetic
examples establish software behaviour rather than market demand.

## Verifying retained captures

~~~powershell
keywordmoves monitor --store ./private/evidence.sqlite verify
keywordmoves monitor --store ./private/evidence.sqlite verify --capture-root ./private/exports
~~~

The first command verifies SQLite and the hashes of retained raw snapshots and
normalised observations. The second additionally checks bounded local capture
files under the explicit permitted root. References outside that root are never
read; missing files, missing expected hashes and mismatches are reported.

With a capture root, the overall verified result requires every referenced file
to pass. No capture references also produces a non-success result, rather than
claiming that external files were checked. Database verification is reported
separately. File hashing establishes byte identity, not accuracy, provenance
rights or observed search demand.

## Compatibility with reviewed browser observations

The established keywordmoves-observations/v1 format and observed-evidence
PluginResults retain their original raw bytes. Their older native-autocomplete,
native-caption-language and native-search-sample labels map to
language_suggestion; sampled-language remains a qualitative observation.
third-party-estimate maps to search_volume_estimate with approximation marked.

Unavailable display text moves into notes and the measurement becomes null.
Limits, seed phrases, censoring and original labels remain recorded. This
compatibility mapping does not apply to new canonical monitor observations,
which must already use the documented strict schema. Unknown labels still fail
validation; imports never guess a new platform's metric meaning.
