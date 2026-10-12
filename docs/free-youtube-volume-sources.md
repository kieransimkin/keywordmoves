# Free YouTube search-volume source audit

Update, 12 October 2026: [automatic collection](automatic-free-collection.md)
adds bounded YT Keyword Tool public-form JSON retrieval and connected-browser
collection of Keywords Everywhere Google context. The public vidIQ HTTP
collector stays unavailable after a fresh robots-policy 429; no bypass is
provided. The dated audit below preserves the earlier findings.

For later native testing of suggestion/result tools and the additional working
imports, see [free public-source modules](free-public-source-modules.md). That
follow-up holds Navid and Keyword Tool Dominator's autocomplete claims after
control-string expansions, records unavailable Rapidtags/DigestSEO routes, adds
YT Keyword Tool's suggestion importer, and enables free Keywords Everywhere
Google planning context without treating it as YouTube volume.

Reviewed 11 October 2026. Free access and reliable measurement are separate
questions. This audit found another usable public access route to vidIQ's
provider estimates. No additional independent provider passed the current
no-account access and basic numerical checks. This is a dated result, not a
claim that no such provider could ever exist.

## Working public feed

[vidIQ's public keyword page](https://vidiq.com/features/keyword-tools/) displayed
five US monthly estimates and five rising queries without a new sign-in.
Capture its rendered overview section, retain the original bytes and source
date, then use:

```powershell
keywordmoves run vidiq --operation import-public-html --input public-widget.html `
  --option observed_at=2026-10-11 `
  --option source_url=https://vidiq.com/features/keyword-tools/
```

The source page and renderer must be reviewed before saving the capture. The
explicit source URL is a declaration, not cryptographic proof of origin. This
operation imports a saved HTML page or overview section and makes zero network
requests. HTML parsing needs the existing `online` optional extra.

The adapter reads only the labelled overview and visible rows. It validates the
section structure, rank order, displayed units, US scope, seven-day growth
context and update date. Missing/changed/duplicated structures fail rather than
falling back to arbitrary page text. Input is bounded to 5 MB and each section
to 100 rows. Embedded scripts and explicitly hidden content are ignored.

- Top counts remain `public_monthly_search_estimate`, `searches_per_month`, US.
  The page does not specify its averaging period, so that detail stays unknown.
- Abbreviations such as `1.2M` remain strings with `rounded=true`. Metadata keeps
  a nominal display-scaled value for inspection; it is not the unrounded count.
- Rising percentages remain `public_search_growth`, `percent_growth`, past
  seven days versus the previous period. Their geography is not stated by that
  section and stays null; US is not copied from the top-count section.
- Displayed update dates stay separate from capture dates. Missing dates remain
  unknown; an old displayed date remains stale, and future dates fail. A current
  displayed date is the provider's statement, not an independent accuracy test.
- Scores remain null. These featured terms cannot answer arbitrary seed queries
  and are not a second independent model alongside vidIQ's account data.

Compose `vidiq/import-public-html` in existing `research-batch` plans. Run
`python examples/vidiq_public_demo.py --output-dir public-demo` for a synthetic
offline example. No public-feed scraper, login automation or hidden API is added.
A bounded direct robots-policy request was rate-limited during this audit; it
was stopped without retry or alternate-client bypass. Native reviewed captures
remain the supported working route.

## Other candidates

| Source | Finding | Use in KeywordMoves |
|---|---|---|
| [Ahrefs](https://ahrefs.com/youtube-keyword-tool) | Native free YouTube form under maintenance | Unavailable; do not substitute its Google tool |
| [Keyword Tool](https://keywordtool.io/api) | YouTube volume requires paid access; Guest is Google/Bing | Existing authorised provider imports; no free YouTube-volume route claimed |
| [Keywords Everywhere](https://keywordseverywhere.com/tools/youtube-keyword-tool/) | Explicitly enriches YouTube suggestions with Google monthly volumes | Google context only; never label these numbers YouTube counts |
| [Keyword Keg](https://keywordkeg.com/) | Native route points to the replacement Keywords Everywhere tools | Same underlying route, not a new independent volume provider |
| [TubeBuddy](https://tubebuddyhelp.zendesk.com/hc/en-us/articles/53959062030739-How-is-of-Searches-Per-Month-in-Keyword-Explorer-Calculated) | Documents Keyword Tool as monthly-estimate supplier; account/extension route not configured here | Access-dependent and not independent of Keyword Tool |
| [TubeRanker](https://tuberanker.com/keyword-tool) | Native security-verification gate | Access blocked; no bypass |
| [KeySearch](https://www.keysearch.co/tuberesearch/youtube-keyword-tool) | Public marketing page supplies no query form; account/trial route | No guest query result verified |
| [Fulhar](https://www.fulhar.com/youtube-keyword-research-tool) | Research link redirects to login; page describes a SERP/view-tail model | Account-dependent, underlying model unverified; no count imported |
| [RyRob](https://www.ryrob.com/youtube-keyword-tool/) | Returned thousands of monthly searches for a newly invented random-token control | Quarantined numerical claims; no demand import |
| [FreeKWSearch](https://freekwsearch.com/) | Same random-token control produced thousands; figures closely match RyRob's displays | Quarantined; agreement does not demonstrate independence |
| [InstantViews](https://instantviews.net/search-volume) | Repeated random-token query changed by thousands immediately | Quarantined numerical claims; no demand import |
| [TubeCheckers](https://www.tubecheckers.com/keyword-research/) | Same five volume figures on two very different channels | Failed basic source review; retain audit evidence only |
| [ToolXP](https://toolxp.org/tools/youtube-keyword-search-volume-checker/) | Submitted query produced no volume result | Unavailable; not a working integration |
| [H-Supertools](https://h-supertools.com/youtube/youtube-keyword-tool) | Native origin-certificate error | Unavailable; no TLS bypass |
| [Keyword Tool Dominator](https://www.keywordtooldominator.com/youtube-keyword-tool) | Describes relative popularity rather than monthly counts | Discovery language only |
| [YT Keyword Tool](https://ytkeywordtool.com/) | Explicitly reports autocomplete ranking, no absolute volume | Discovery language only |
| [DigestSEO](https://digestseo.com/youtube-keyword-tool/) | Public result statistics, explicitly no monthly volume | SERP context only |
| [Rapidtags](https://rapidtags.io/keyword-research) | Independently derived 0..100 scores from public video data | Indices/context, not monthly query counts |
| [Navid](https://navid.me/free-tools/youtube-keyword-tool) | Explicitly shows suggestions/results with no volume | Discovery context only |
| [YTTools Pro](https://www.yttools.pro/keyword-generator) | Directional popularity and AI ideas, not official volume | Proposals/context only |

Google Trends' free YouTube Search exports remain independently useful relative
interest evidence through the existing YouTube adapter; see
[YouTube demand sources](youtube-demand-sources.md). The official YouTube Data
API and channel Analytics operations remain result/referral evidence, not a
public market-wide query-count feed.

## Admission checks for a new source

Check the exact engine, country, period, units, data origin, update date, rights
and free-access limits. Test a real seed, a second seed, a newly invented
negative-control token and a repeat. A nonsensical string generating substantial
volume, instantaneous large changes without a stated basis, fixed figures
across unrelated inputs or repurposed Google data should trigger review before
admission. These checks can expose problems; passing them does not prove accuracy.

Keep rejected raw observations in a dated audit, separate from target demand
records. Do not compensate for missing estimates with guessed zeros, generic
keyword expansion, paid fallback or an invented Trends-to-volume conversion.
