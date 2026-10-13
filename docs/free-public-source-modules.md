# Reviewed free public-source modules

The original operations import saved, reviewed browser HTML. They make no HTTP requests,
create no accounts and spend no API credits. Install `keywordmoves[online]` for
the HTML parser. Source capture remains a separate permitted browser operation;
there is no hidden endpoint or rate-limit workaround. Since 0.5.5,
[automatic collectors](automatic-free-collection.md) also fetch YT public-form
JSON and operate supported browser forms, handing results directly to the
existing parsers. Manual HTML capture/import is optional for these routes.

## Working routes

| Module / operation | Data retained | Meaning and limits |
| --- | --- | --- |
| `vidiq/import-public-html` | Public featured monthly displays and rising keywords | US rounded estimates and separate seven-day growth; the rising geography is unspecified. Same vidIQ model, no arbitrary seed queries. |
| `ytkeywordtool/import-public-html` | Visible suggestions, category, High/Medium/Low labels and presentation order | Provider-reported autocomplete. Tiers are relative to the captured batch, not counts, country demand or cross-batch scores. |
| `keywords-everywhere/import-public-html` | Displayed Google monthly estimates, CPC, paid-ad competition and growth | Google planning context for phrases from the public tool. No YouTube volume. Rows with all planning values unavailable are excluded. Suggestion origin is an unverified provider claim. |
| `google-trends/import-interest`, `import-related` and `collect-browser` | Native YouTube Search CSV, completed-window summaries and related-query Top/Rising/Breakout | Native browser-download collection is automatic in 0.5.6. Relative interest/growth only; zero can mean below the reporting threshold. |

On 11 October 2026, YT Keyword Tool returned 113 phrases for a music seed and
reported no suggestions for a newly invented control string. This supports using
its visible suggestions as a research queue; it does not independently certify
every suggestion or tier. The public Keywords Everywhere tool returned Google
planning displays for common music phrases and unavailable values for the
invented string. Its autocomplete expansion also produced control-string
phrases, so this module imports measured Google planning context only and does
not certify those phrases as genuine observed YouTube searches.

## Capture and run

Save the current rendered result HTML, after reviewing the displayed query and
scope. Ordinary server HTML may contain no results. For YT Keyword Tool save the
`#tool-results` section, or the rendered main/page HTML containing it. An error
page can retain old results hidden in the DOM: the importer rejects those hidden
results instead of recovering an old successful batch.

```powershell
keywordmoves run ytkeywordtool --operation import-public-html `
  --keyword "your reviewed seed" --input ./ytkeywordtool-results.html `
  --option source_url=https://ytkeywordtool.com/ --option observed_at=2026-10-11

keywordmoves run keywords-everywhere --operation import-public-html `
  --input ./keywords-everywhere-results.html `
  --option source_url=https://keywordseverywhere.com/tools/youtube-keyword-tool/ `
  --option observed_at=2026-10-11 --option country=GLB --option currency=USD
```

For YT Keyword Tool, the caller confirms the seed because live input values may
not be represented in serialized HTML. The tool has no declared country control;
the module leaves geography null. It requires one seed, one file, the exact
official source URL and an ISO capture date/time. Only `source_url` and
`observed_at` are accepted options.

For Keywords Everywhere, confirm the actual selected Google country and CPC
currency. `country` is an uppercase two-letter code, or `GLB` for its Global
selection. Supported display currencies are USD, CAD, AUD, NZD, GBP, EUR, INR and
ZAR; currency symbols must agree with the supplied code. A dollar sign alone
cannot identify the currency, so the caller must review it. Only `source_url`,
`observed_at`, `country` and `currency` are accepted options. Save the current
rendered main/page HTML containing the explicit Google-volume label and table.

Both imports are bounded to 5 MB, preserve the exact capture hash, reject
duplicate/ambiguous rows and fail when reviewed labels or columns change. They
ignore scripts, templates, explicit hidden attributes, `aria-hidden`, the
`hidden` utility class and inline hidden styles. They do not calculate external
stylesheets' visibility. Review the capture before import.

## Metric semantics

`ytkeywordtool` returns `provider_autocomplete_tier` as a string with unit
`within_batch_label`, and `displayed_order` as an ordinal. `score` remains null.
The count in the source is a count of returned suggestions, not search demand.
A filtered/partial capture remains partial when fewer rows are visible than the
reported batch total.

`keywords-everywhere` explicitly sets candidate `platform` and `metric_platform`
to `Google`. Its metrics are `google_estimated_search_volume`, `google_ads_cpc`,
`google_paid_competition` and `google_search_growth_display`. Monthly volume is the
provider's average over the preceding 12 months, with exact calendar dates
unspecified. Growth is the displayed last-month comparison with the average of
the preceding 11 months; it may be rounded. It is not seven-day YouTube growth.
Missing displays remain null; actual displayed zero stays zero. SVG trend charts
are not converted back into monthly values. Partial harvests, page limits and
filtering mean completeness stays partial. The source supplies no measurement
timestamp, so the capture date is never promoted to a provider update date.

Neither module creates a universal demand or ranking score. Review song intent,
language, audience and misleading modifiers with `candidate-focus` before adding
a phrase to a target register.

## Composition and example

Both operations compose through `research-batch/collect`. Keywords Everywhere's
live `metrics` API stays excluded from batches; it may be metered. Existing
dedicated API use remains unchanged and still requires authorised access.

Run the offline synthetic example:

```powershell
python examples/free_public_sources_demo.py --output-dir ./private-demo
```

The example writes two clearly synthetic HTML captures, a batch plan and results.
Real source/account captures are never bundled in public fixtures or releases.

## Reviewed routes not integrated

The [22-service volume audit](free-youtube-volume-sources.md) retains the wider
access and quality findings. A later useful-data check covered actual public
suggestion/result routes too:

| Source | Current result and reason |
| --- | --- |
| Keyword Tool Dominator | Returned common phrases but also many expansions for the invented control. Its source-derived score/rank claims need verification; not imported as reliable observed search data. Two permitted daily searches were used without resetting the limit. |
| Navid | Returned 256 suggestions for the music seed and 152 for the invented control, plus loosely related result context. The claimed autocomplete basis needs verification before a dedicated search-data module is justified. |
| Rapidtags | Public page showed demonstrations; live research links require an account. No new account was created, and demonstration scores are not imported as current results. |
| DigestSEO | Native query explicitly reported that its server-side YouTube API key is unavailable. No result rows or private channel connection were available. |
| YTTools Pro | Documentation labels the generator as AI ideas with directional scores. No independently sourced demand data was established; use explicit proposal/LLM routes for generated ideas. |
| Ahrefs, Keyword Tool, Google Trends | Already have named modules. Free Ahrefs form remained in maintenance; live Keyword Tool YouTube estimates require paid access; Trends' export route works as relative interest. |
| Other volume claims and gated/no-output tools | No new reliable data route passed. Keep the audit evidence and explicit unavailability rather than adding placeholder modules or unverified counts. |

Negative controls raise questions; they do not prove fraud or reveal a provider's
implementation. The native YouTube comparison used the existing GB session,
while some third-party tools declared US scope, so it was not a like-for-like
country benchmark. No additional independent free YouTube absolute-volume
provider passed this review.

Primary documentation: [YT Keyword Tool](https://ytkeywordtool.com/),
[Keywords Everywhere's public tool](https://keywordseverywhere.com/tools/youtube-keyword-tool/),
[Keyword Tool Dominator](https://www.keywordtooldominator.com/youtube-keyword-tool),
[Navid](https://navid.me/free-tools/youtube-keyword-tool),
[Rapidtags](https://rapidtags.io/keyword-research),
[DigestSEO](https://digestseo.com/youtube-keyword-tool/).
