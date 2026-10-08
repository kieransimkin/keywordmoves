# Reviewed native CSV exports

The **native-export import-csv** operation converts a permitted, reviewed CSV
into standard KeywordMoves evidence. It performs no scraping, account login or
API request. Supply the source's actual column names and semantics.

~~~powershell
keywordmoves run native-export --operation import-csv --input .\native.csv --option platform=Pinterest --option "source=Reviewed Pinterest Trends export" --option metric=search_interest --option unit=index_0_100 --option observed_at=2025-10-07 --option geography=GB --option "scope=Actual reviewed native surface" --option "window=12 months" --option evidence_kind=platform_search_interest --option phrase_column=Query --option value_column=Value --option "subject=Example Song"
~~~

These are example mappings, not a claim that Pinterest uses those CSV headers
or that an account has export permission. Inspect the actual file and current
destination guidance before choosing columns and labels.

Required options: platform, source, geography, scope, window, evidence_kind,
phrase_column, value_column, and a constant or column for metric, unit and
capture date. Supported mappings:

| Option | Meaning |
|---|---|
| phrase_column / value_column | Exact phrase and metric display columns. |
| metric_column / unit_column | Per-row metric names and units instead of constants. |
| date_column | Per-row capture date instead of observed_at. |
| subject_column | Per-row song/topic identity instead of subject. |
| geography_column | Per-row region instead of geography. |

Optional context: period_start, period_end, normalization_id, dimensions_json,
completeness, approximate, delimiter. Evidence kinds are documented in
[monitoring](monitoring.md).

Native search interest is platform_search_interest; website/account performance
is property_performance or channel_referral; posts/discussions are content_supply;
suggestions are language_suggestion. Do not label third-party estimates as
first-party data or post counts as searches.

- Numeric zero is retained; missing displays become null/unavailable.
- Less-than values, ranges and other non-exact displays remain strings.
  Explicit censoring is retained; volume estimates are approximate by default.
- Percent signs require an explicitly compatible unit.
- Metrics are never converted between providers or units.
- Ambiguous headers, missing columns, malformed/truncated rows, non-finite values
  and unreadable/non-UTF-8 files are rejected.
- Bounds are 5 MB and 50,000 rows. Truncation is never presented as completeness.
- The original CSV hash, path and mappings are retained. Preserve that file.
- Completeness defaults to unknown; a complete file is not an exhaustive platform
  population and does not remove source sampling/privacy restrictions.

For JSON use the existing observed-evidence plugin or the monitoring schema.
For supported Instagram, TikTok, YouTube, Reddit, Google and Bing exports,
prefer their named source modules.

## Platform coverage

| Platform | Existing route | Remaining demand limitation |
|---|---|---|
| Google | Trends, planning/provider estimates, GSC, native observations | Relative interest, estimates and property exposure remain distinct. |
| Bing | Webmaster keyword/history/related reports and planning providers | Actual account access and responses need verification. |
| YouTube | Analytics referrals, Studio observations and provider estimates | Channel referrals are not total platform volume. |
| Instagram | Dedicated module, account/media observations and provider estimates | Posts and engagement are not searches. |
| TikTok | Dedicated module, reviewed Creator Search Insights and provider observations | Personalised feature access and metric windows vary. |
| Reddit | Dedicated permitted imports/reads and licensed native observations | Discussions are not searches; Pro download permissions matter. |
| Pinterest | Reviewed Trends/search-interest files or observations | No new live native API is claimed by this importer. |
| Facebook | Permitted native reports/observations | No global organic keyword-search counter is inferred. |
| X | Permitted query/post-count reports or observations | Conversations and posts measure content supply. |
| LinkedIn | Permitted search/account analytics observations | Account discovery is not global keyword volume. |
| Yahoo / DuckDuckGo | Native suggestions/results and declared tool observations | Result language is not measured volume. |

All twelve can participate in watchlists, freshness reports and evidence history.
That tracks coverage without promising a common demand metric across networks.
