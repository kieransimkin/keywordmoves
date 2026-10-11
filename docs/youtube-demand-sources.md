# YouTube demand sources

Checked against official documentation on 11 October 2026. Access, estimates
and pricing can change; preserve source, geography, dates, units and limitations.

| Source | Access | Measurement | Main limit |
|---|---|---|---|
| Google Trends, YouTube Search | Free native UI and CSV export | Sampled relative interest, 0..100 | No absolute query counts; comparison sets change normalisation |
| vidIQ | Limited native free results; other routes depend on account/credits | Provider 30-day estimates and indices | Imported estimates remain attributed to vidIQ |
| Keyword Tool YouTube | Paid Pro/API access | Average monthly provider estimates over the last 12 months | Independent provider, different measurement window; CPC/competition are Google Ads proxies |
| Keyword Tool Guest MCP | Free limited Google/Bing results | Cached estimates for supported engines | Does not provide YouTube estimates |
| Ahrefs free YouTube tool | Public UI was under maintenance at review | Provider estimates if service available | Availability and usable scope must be verified live |

Sources: [Google Trends FAQ](https://support.google.com/trends/answer/4365533),
[CSV export](https://support.google.com/trends/answer/4365538),
[Keyword Tool API](https://keywordtool.io/api),
[Keyword Tool Pro and trial FAQ](https://keywordtool.io/pro),
[Ahrefs YouTube tool](https://ahrefs.com/youtube-keyword-tool).
Keyword Tool states there is no free trial. A refund offer requires payment.
An offline importer does not grant access or independently validate a provider's model.

## Free YouTube relative interest

In Google Trends select the exact keywords, geography, reporting window,
category and **YouTube Search** property, then download the native CSV. Retain
the original CSV and separately record the visible filters and capture date;
the CSV alone does not prove the search property. Import through the existing
YouTube adapter:

```powershell
keywordmoves run youtube --operation trends-import --input multiTimeline.csv `
  --option search_property=youtube --option geography=Worldwide `
  --option observed_at=2026-10-11 --option completed_periods_only=true `
  --option scope=exact-five-keyword-comparison --option window=past-12-months `
  --option normalization_id=unique-export-identity
```

`completed_periods_only` is opt-in and defaults to false, preserving existing
imports. It requires an ISO capture date or timezone-aware timestamp through
the direct Trends adapter (the YouTube adapter requires a date). Supported
periods are Week (start + six days), Day/Date and Month (calendar month end).
Only periods ending **before** the UTC capture date enter summaries; this is a
conservative rule that excludes a period still underway on that date. Rows
explicitly marked IsPartial are also excluded. All points, raw values,
eligibility, inferred ends, counts and the completed summary window remain in
the result. Unknown granularity or invalid dates fail rather than guessing.

`<1` remains censored. Missing/censored eligible values suppress exact means and
peaks; a mean of only the uncensored subset would overstate interest. An empty
eligible window remains unavailable. Trends indices cannot be converted into
monthly volumes or compared blindly across independently normalised exports.
The YouTube adapter deliberately leaves candidate scores null.

## Independent provider imports, when authorised data exist

```powershell
keywordmoves run keywordtool --operation import-json --input authorised.json `
  --option platform=youtube --option country=GLB --option observed_at=2026-10-11
```

This route is offline, requires no API key and makes zero HTTP requests. It
accepts documented API JSON with a nonempty `results` object keyed by phrase,
each row containing optional `string`, `volume`, `cpc`, `cmp`, `trend` and monthly
fields `m1`..`m48`, `m1_month`, `m1_year`, etc. Monthly numeric values require
valid date pairs. Missing values remain null; actual numeric zero is retained.
JSON must contain numeric metrics rather than display strings. Invalid,
non-finite, negative volumes/CPC, out-of-range competition, duplicate normalised
phrases/months, error responses and credentials are rejected. Input is bounded
to 20 MB and 10,000 keywords. A partial notice retains missingness.

Alternatively use this reviewed capture envelope:

```json
{"schema":"keywordmoves-keywordtool-capture/v1",
 "observed_at":"2026-10-11",
 "request":{"platform":"youtube","country":"GLB","currency":"USD"},
 "result":{"results":{"example song":{"volume":1200,"cpc":0.5,"cmp":0.25,
 "trend":-0.1,"m1":1000,"m1_month":9,"m1_year":2026}}}}
```

This is synthetic data. Conflicting CLI/capture scope fails. `GLB` means
worldwide; a two-letter country code remains country-specific. Currency
defaults to USD; declare the actual export currency. Capture date does not
establish provider refresh date, so freshness stays unknown. Dated monthly
history is preserved separately, with unfinished/future months flagged.
Volume is an average monthly estimate over 12 months, separate from vidIQ's
30-day estimates. Google Ads CPC and competition remain named advertising
proxies; neither establishes YouTube organic difficulty. Candidate score is null.

The existing live API supports YouTube `country=GLB` as documented, but requires
authorised paid access. Tests use mock transport; no live paid accuracy is
claimed. `research-batch` permits only `keywordtool/import-json`; suggestions
and metrics operations are rejected before collection because they may bill.

Run `python examples/youtube_demand_demo.py --output-dir demand-demo` for a
reproducible synthetic, no-network example. No account creation, subscription,
credential storage, automatic paid fallback or universal demand score is added.
