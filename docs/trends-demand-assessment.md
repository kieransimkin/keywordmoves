# Native relative-demand assessment

Google Trends scales each query/export independently. Peaks of 100 in separate
exports do not establish equal demand. Collect terms together with the same
geography, dates, category and search property to compare their relative indices.
Classic Explore supports five groups; this collector supports 2-5 plain terms,
without grouped terms, topic IDs, per-term overrides or cross-export calibration.

```javascript
const {collectAndAnalyseTrends} = await import("file:///absolute/path/to/keywordmoves/browser/google_trends.mjs");
const result = await collectAndAnalyseTrends(tab, {
  seeds: ["friendship songs", "grief songs", "night driving music"], country: "GLB",
}, {python: "/absolute/path/to/python"});
```

Use `seed` for the existing single-term route or `seeds` for 1-5 terms, never both.
Multiple terms support only `chart=interest`; related queries stay single-term.
Native URL, visible inputs/search-term types and every CSV column must match.
Comparison receipts use `keywordmoves-trends-browser/v2`; single receipts remain
v1. Python `google-trends/collect-browser` accepts up to five explicit keywords
through an authorised context driver. The same bounds and access/handoff rules
apply as [automatic collection](automatic-free-collection.md). Visible provider
errors stop; no automatic retry, alternate browser or private endpoint follows.

```powershell
keywordmoves run google-trends --operation assess-interest --input multiTimeline.csv `
  --option search_property=youtube --option geography=GLB --option observed_at=2026-10-13
```

`assess-interest` is offline and accepts explicitly confirmed `youtube` or `web`
exports. CSV alone does not prove the search property: retain filter evidence.
Declare actual geography/date/scope. The existing parser excludes unfinished and
source-partial periods. Each candidate's `metadata.demand_profile` contains:

- `mean_index`: completed-window mean, or null when missing/censored values
  prevent an exact mean. Candidate `score` remains null.
- `mean_index_bounds`: bounds derived solely from `<1` displays, rounded outward
  to four decimals. Explicit missing values prevent these bounds. They are
  reporting bounds, never confidence intervals or monthly search counts.
- `positive_periods`, `threshold_zero_periods`, `unknown_or_censored_periods`:
  persistence of the display. Threshold zero is not proof of no searches.
- `normalization_id`, `comparison_terms`, `within_export_comparable`, `cohort_rank`:
  dense ranks of available exact means only inside that exact shared export and
  completed window. Single-series and unknown means have no cohort rank.
- `absolute_monthly_searches`: null. Indices do not supply absolute volume.

Small isolated spikes and statistical noise remain limitations. Do not turn a
relative rank/persistence into a popularity, audience-size, growth, ranking or
campaign-effect prediction. Never compare independently normalized exports,
countries or properties. Provider volumes, Google planning, owned impressions,
autocomplete tiers and lexical salience retain their separate native units.

```powershell
python examples/trends_demand_assessment.py
python -m pytest tests/test_trends_browser.py
node --test tests/browser_google_trends.test.mjs
```

Primary sources: [comparison guide](https://support.google.com/trends/answer/4359550)
and [normalization, thresholds and noise](https://support.google.com/trends/answer/4365533).

For recurring events, the separate offline `plan-event-cycles` operation adds
source-bound calendar alignment, quality/intent separation, chronological
whole-cycle holdouts and conditional date-window proposals. Read
[the event-cycle contract](event-cycles.md). Existing `assess-interest` summaries
remain unchanged; their relative indices do not become absolute searches or a
learned publishing hour.

## Visible source display failure

A normal query can display “Oops! There was a problem displaying this page”.
Collection records source-data unavailable instead of waiting for a missing
chart or re-querying. No authoritative matching diagnosis was established in the
13 October review; this is not proof of a browser, account or network cause.
Keep an earlier valid export as earlier evidence rather than claiming a failed
refresh updated it.
