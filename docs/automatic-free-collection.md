# Automatic free-source collection

The reviewed free sources can now feed KeywordMoves without a person saving or
importing HTML. Evidence retains its original source, scope and units.

| Source | Automatic route | Current boundary |
| --- | --- | --- |
| YT Keyword Tool | `ytkeywordtool/collect-public`, its public form's JSON response | Free public-client route, not an official developer API. Suggestions, batch scores and tiers remain provider claims, not monthly volume or country demand. |
| Keywords Everywhere free tool | Packaged connected-browser helper operates its form and hands the table directly to the parser | Requires an authorised browser tab. Source pacing, CAPTCHA processing and shared free quota remain authoritative. Google planning context only. |
| vidIQ public featured feed | `vidiq/collect-public`, robots check then fixed page fetch | Live robots request returned HTTP 429 on 12 October. Implemented and tested, but live retrieval is currently unavailable. No page request, retries or client/browser fallback. |
| Datamuse, Wikipedia, Wikimedia pageviews, iTunes catalog | Existing free API operations and research batches | Lexical, topic, attention or catalog evidence, never YouTube volume. |
| Google Trends YouTube Search | Existing CSV import and completed-period summaries | Relative-interest export route retained. No unofficial scraper or unavailable restricted official API is added; CSV exports are separate from the manual HTML issue addressed here. |

## Direct collection

Install `keywordmoves[online]`. No input file, API key, account or paid call is
required for these public operations:

```powershell
keywordmoves run ytkeywordtool --operation collect-public `
  --keyword "songs about friendship" --option limit=200 --format json

keywordmoves run vidiq --operation collect-public --option max_age_days=30
```

Options: `max_requests` (default/minimum 2), `max_response_bytes`, `timeout`,
`min_interval`, `limit`. vidIQ additionally accepts `max_age_days` and has no
arbitrary-seed queries. The two requests are robots and data; at least ten
seconds separate them, extended by provider policy. Redirects, automatic retries,
access failures, excessive responses and changed schemas stop collection.

YT Keyword Tool's linked public browser application uses `/api/keywords?q=...`.
Its [terms](https://ytkeywordtool.com/terms) prohibit excessive/abusive automated
traffic. One explicit invocation makes one query; no source sweep, reverse
engineering or alternate client follows failure. The response must echo the
requested input. An effective seed rewritten by the provider stays separate.
Counts, duplicates, categories, tiers and numeric fields are checked. An output
limit does not restrict the work performed inside a provider query. Raw response
hashes and retrieval dates are retained; unknown measurement dates remain unknown.

## Connected-browser collection

The wheel includes `keywordmoves/browser/free_public.mjs`. It uses only a
caller-provided authorised, dedicated idle tab with `goto`, `reload`, `url` and
documented `playwright` UI/read-only DOM methods. Follow the host's browser
instructions and source permissions. It does not start another browser, inspect
profiles/storage, call private endpoints, sign in or solve challenges.

Find the installed helper:

```powershell
python -c "import keywordmoves.browser, pathlib; print(pathlib.Path(keywordmoves.browser.__file__).with_name('free_public.mjs'))"
```

In a host permitting local subprocesses, collection and analysis are one call:

```javascript
const {collectAndAnalyse} = await import("file:///absolute/path/to/keywordmoves/browser/free_public.mjs");
const result = await collectAndAnalyse(tab, {
  source: "keywords-everywhere", seed: "songs about friendship",
  country: "GLB", currency: "USD", maxRows: 30, maxHarvestSeconds: 20,
}, {python: "/absolute/path/to/python"});
```

Node 20+ is supported. `collectPublic(tab, request)` returns an automatic JSON
receipt; `analyseCapture(receipt, {python})` streams it to `python -m
keywordmoves.browser_capture` through stdin. The existing parsers run in memory;
no HTML file or duplicate metrics engine is needed. `cwd` is an optional local
development setting. `collectAndAnalyse` also accepts an `analyse` callback for
a host-provided permitted local bridge. If a host denies subprocesses, retain
`error.capture` and forward that receipt through its supported local execution
tool to the same Python module. A local analysis failure must never trigger
another source query. Saved receipts can be replayed, but replay is not fresh
collection.

The collector waits for the visible quota banner and refuses unknown/exhausted
quota before submitting. Defaults: one seed, 20 seconds harvesting and a stop
threshold of 30 displayed phrases. Thresholds can overshoot while a source query
is in flight. It preserves source pacing, stops an owned harvest on failure,
waits for the stopped display, and captures up to its 200-row page selection.
Actual underlying request counts and concurrent usage in other clients are
unknown. This is not a strict request budget or global daily-quota reservation;
the source's shared free quota remains authoritative. Never reset quota/session
state or switch clients to evade a limit. Existing running harvests remain with
their owner.

Supported country/currency pairs: GLB/USD, US/USD, GB/GBP, CA/CAD, AU/AUD, IN/INR,
NZ/NZD, ZA/ZAR. Selected country and seed are checked before and after collecting.
The normal source `?job=<UUID>` result URL is allowed and recorded; other
navigation is rejected. The receipt retains canonical and actual page URLs,
seed, capture hash/date and partial-harvest status. The parsers validate the
visible currency/Google-only column labels, reject hidden stale results and
exclude all-unmeasured rows. These checks do not certify a caller-supplied
browser's authenticity.

The helper also supports `ytkeywordtool` where ordinary UI collection is needed.
There is no vidIQ browser fallback after a failed HTTP policy check. Visible
source errors, login/challenge pages, quota failures, timeouts, scope changes
and unsettled results stop collection. Source request counts remain unknown;
capture time never becomes a provider measurement date.

Python applications can supply `ExecutionContext.public_browser` with a
synchronous `collect(source, request) -> receipt` driver. The existing named
`ytkeywordtool/collect-browser` and `keywords-everywhere/collect-browser`
operations validate the requested seed/source/scope and receipt. The CLI alone
does not discover/control an in-app browser or start a headless session; it
accurately reports a missing driver.

## Composition and verification

`research-batch` accepts these free `collect-public` and `collect-browser`
operations. Browser tasks require the supplied context driver; missing access
becomes an unavailable task. Paid Keywords Everywhere `metrics` stays excluded.
Explicit plans do not start background refresh, seed sweeps, retries, pagination
or provider fallbacks.

```powershell
python examples/automatic_free_collection_demo.py
node --test tests/browser_free_public.test.mjs
python -m pytest tests/test_automatic_free_sources.py
```

The 12 October live registered YT operation returned 107 music suggestions. The
actual browser helper collected 21 displayed Keywords Everywhere rows; 19 Google
context rows were retained, with two wholly unavailable rows excluded. The
in-app host denied a subprocess, so the same SDK analysed the automatic JSON
receipt through permitted local shell execution without a source re-query.
vidIQ stayed unavailable after its robots 429. No paid calls, new accounts,
quota resets or manual HTML imports were used. Real captures stay private;
tests/examples use synthetic data.
