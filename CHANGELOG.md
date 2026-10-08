# Changelog

## 0.4.2 - 8 October 2026

- Added PyPI trusted publishing through the configured `release.yml` workflow
  and `pypi` environment. Publication verifies the immutable source tag,
  package versions and checksums of the already tested GitHub distributions.
- The exact GitHub wheel and source archive are uploaded without rebuilding
  different packages or storing registry credentials in source.

## 0.4.1 - 8 October 2026

- Corrected YouTube Analytics setup to require both `youtube.readonly` and
  `yt-analytics.readonly`, following the current `reports.query` contract.
- Added an authenticated-access guide with separate browser/connector/API
  evidence states, secure Windows credential injection, first-party scope
  boundaries and finite read-only smoke examples.
- Linked collector setup to the credential guide. OAuth creation, consent,
  renewal, platform approval and credentials remain external to KeywordMoves;
  no account data or credentials are bundled.

## 0.4.0 — 8 October 2026

- Import established reviewed browser-observation labels without treating sampled language as demand; verify bounded local capture hashes with separate database status.
- Added private SQLite watchlists, hashed input captures, source-specific
  history, coverage/freshness reports, local change alerts and acknowledgement.
- Added due plans and exact-plan opt-in composition of existing first-party
  readers, with transactional per-account request reservations, no paid routes,
  no retry and explicit interrupted-run recovery.
- Added reviewed CSV imports with explicit platform/metric mappings, preserving
  zero, missing, rounded/censored and qualitative values separately.
- Corrected Trends related-query deduplication and metric units: Top indices,
  Rising percentages and Breakout labels remain distinct. Added the shared
  Google Search related-export route.
- Preserved censored/partial interest-over-time points without inventing exact
  summary values, including explicit YouTube search-property scope.
- Added retention cleanup, integrity checks, safe local HTML reporting, adopter
  documentation and a reproducible synthetic twelve-platform example.

Migration: related-query metrics now distinguish related_top/index_0_100,
related_rising/percent_growth and related_rising_breakout/growth_label. The same
phrase may appear in both Top and Rising; related candidates have no score.
Interest series containing censored/missing/partial points retain their raw
points but no exact mean/peak summary. No campaign register is modified.

## 0.3.2 - 2026-10-08

- Add an original tool-specific vector logo and PNG companion in the shared DanceFlow visual style.
- Clarify package descriptions from reviewed documentation and KeywordMoves literal-source evidence, without claims of measured search demand.
- Link package descriptions and READMEs to Kieran Simkin’s website and retain branding files in installable packages.


## 0.3.1 - 2026-10-07

- Install the online extra in the full CI matrix so saved-HTML importer tests run with their declared dependencies.
- Validate NLTK source spans against untranslated source line endings in the pretrained-model test, including Windows checkouts.
- Keep the extractor's source offsets and runtime behaviour unchanged.

## 0.3.0 - 2026-10-07

- Add `text-library extract-literal` for contiguous Unicode phrases with source character spans and corpus occurrence counts. Preserve line, punctuation and file boundaries, internal stopwords, Windows line endings, Persian joiners and numeric names. Keep legacy extraction compatible.
- Preserve subject, seed and capture lineage in reviewed browser observations. Reject non-text lineage fields.
- Align package, command-line and distribution versions, with regression checks.
- Document the new operations with reproducible offline examples and clarify that text counts and sampled search language do not establish demand.
- Replace resolved machine-specific README troubleshooting history with concise integration limits.

Validation: Ruff passes; the Python 3.13 suite completes with 1,821 passed and two optional skips. Source distribution and universal Python wheel build successfully.
