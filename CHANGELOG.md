# Changelog

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
