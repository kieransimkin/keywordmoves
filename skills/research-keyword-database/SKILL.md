---
name: research-keyword-database
description: Inspect, validate, expand, and maintain the canonical My Songs keyword-target register using KeywordMoves and dated search evidence. Use for song-keyword audits, seed expansion, lyric-derived discovery, Google Trends imports, evidence reconciliation, and CSV updates; do not use it to publish SEO or social changes.
---

# Research the keyword database

Use this skill to maintain the private evidence register at
`Z:\My Songs\Keyword Targets\keyword-targets.csv` and to expand it with
KeywordMoves. The register records candidates and evidence; it does not itself
authorise public page, metadata, advertising, or social-platform changes.

## Canonical inputs

Read these before changing the register:

- `Z:\My Songs\AGENTS.md`, especially **Song keyword targeting**.
- `Z:\My Songs\Keyword Targets\README.md` and the current CSV.
- `Z:\My Songs\Releases\EPK Batch 2026\my-songs\catalogue.json` for exact song
  titles, release dates, public state, and EPK targets.
- The relevant release records. For lyric-derived work, follow the release's
  `canonical-lyrics-provenance.json` and reject superseded lyric candidates.
- `Z:\My Songs\Tools\keywordmoves\README.md` and its `## Potential problems`
  section.

Inspect the existing rows for the selected song, phrase, seed, platform, and
target surface before researching. Preserve earlier evidence and decisions; do
not recreate a row that already represents the same song, exact phrase, and
platform scope.

Read [references/register-schema.md](references/register-schema.md) before
adding, merging, validating, or changing rows.

## Choose the research mode

### Inspect or validate

Reconcile the CSV with `catalogue.json`. Confirm every catalogue song has its
placeholder or researched rows, exact catalogue metadata remains current, the
header and encoding are preserved, dates are ISO `YYYY-MM-DD`, and non-empty
keyword rows are deduplicated by normalised song, exact phrase, and platform.
Report counts and discrepancies without interpreting unavailable evidence as
zero.

### Expand a text library

Use `extract-literal` for exact contiguous source wording. It preserves Unicode
letters, numbers, combining marks, internal apostrophes, Persian joiners and
source character offsets, including Windows line endings. Read
`docs/literal-text.md`; legacy `extract-local` retains filtered-token
associations and must not be labelled literal lyrics. Corpus counts do not
establish external search demand.

For catalogue-wide browser observations, preserve the subject, seed, capture
reference and SHA-256 through the optional lineage fields documented in
`docs/browser-observations.md`. Capture and review each platform independently;
loading skeletons and missing observations are not zero demand.

Use KeywordMoves' transparent local extractor first when frequency and n-gram
candidates are enough. This is the lower-compute route:

```powershell
$keywordmoves = 'Z:\My Songs\Tools\keywordmoves\.venv\Scripts\keywordmoves.exe'
& $keywordmoves run text-library `
  --operation extract-literal `
  --input '<canonical-text-path>' `
  --option limit=20 `
  --format json
```

When semantic expansion would materially add related words or concepts, use the
LLM-backed operation and explicitly select the LLM plugin and model:

```powershell
& $keywordmoves run text-library `
  --operation extract `
  --input '<canonical-text-path>' `
  --llm huggingface-transformers `
  --model Qwen/Qwen2.5-0.5B-Instruct `
  --option limit=20 `
  --option 'llm_local_files_only=true' `
  --option 'llm_cache_dir=Z:\My Songs\Tools\keywordmoves\.keywordmoves-cache\huggingface' `
  --format json
```

Do not silently choose an LLM. If another installed LLM plugin is selected,
record its plugin, model, revision, and relevant inference options. Local text
frequency is corpus evidence only. LLM output is a proposal source only; neither
establishes search demand, competition, trend direction, ranking potential, or
platform suitability.

### Import Google Trends evidence

Export the comparison from the supported Google Trends interface, retain the
unmodified CSV as evidence, and pass the actual access date and geography:

```powershell
& $keywordmoves run google-trends `
  --operation import-interest `
  --input '<google-trends-export.csv>' `
  --option geography=GB `
  --option observed_at=YYYY-MM-DD `
  --format json
```

Use `import-related` for a related-query export. Preserve the plugin's metric,
unit, notes, geography, date, source file, and comparison context. A Trends
`index_0_100` value is relative interest within that export, not search volume.
Record insufficient-volume or empty related-query results as unavailable.

### Research demand and audience language

Treat KeywordMoves candidates as a research queue. For each plausible phrase,
seek current evidence from the sources required by `AGENTS.md`: Search Console
when accessible, Google Trends, Google Search suggestions or related searches,
at least one other current public keyword source, and relevant platform-native
discovery surfaces. Keep every platform's evidence separate. Record access date,
geography or account/public scope, metric and unit, limitations, rejected
avenues, and whether the result was unavailable.

Use current primary documentation for access and metric semantics. Do not use
unsupported private endpoints, bypass access gates, or present a third-party
estimate as first-party evidence. Research can be prepared without authorising
OAuth, account changes, paid tools, public changes, or publication.

When a current check comes from a browser-only, authenticated or otherwise
non-exportable surface, record it in a reviewed
`keywordmoves-observations/v1` JSON file and import it with
`observed-evidence --operation import-observations`. Preserve the actual metric,
date, platform, scope and unavailable state. Do not add an unsupported scraper.
If a newly checked source or validation is not yet representable in
KeywordMoves, extend the tool with the smallest reusable, tested capability in
the same run, then use it for the current evidence.

## Decide what enters the register

Add every phrase the analysis identifies as a plausible potential target, even
when confidence is low or demand is unvalidated. Exclude obviously irrelevant,
misleading, sensitive-event-chasing, or song-misrepresenting suggestions; retain
useful rejected tool output in the dated research report instead.

For every retained candidate:

- keep the exact phrase and truthful search intent;
- link it to its seed when one exists;
- distinguish `lyric-derived`, text-extracted, LLM-related, related-search, and
  other discovery methods;
- name the intended page or platform surface and a proportionate proposed use;
- record evidence and limits without collapsing unlike metrics into one score;
- set confidence from the combined fit and evidence, not from model fluency;
- keep `status=proposed` until dated evidence or Kieran's decision supports a
  later lifecycle state.

Do not reproduce unnecessary lyrics in the CSV or report. Record the canonical
source path and a concise theme or wording basis.

## Update safely

Stage CSV changes before replacing the canonical file. Preserve the exact
21-column order and UTF-8 BOM, quote fields correctly, and keep one physical CSV
row per record. Merge new dated evidence into an existing canonical key rather
than silently overwriting history or appending a duplicate. When a status
changes, preserve the previous state and reason in dated notes.

Before committing the replacement, parse the staged file and verify:

- its header exactly matches the schema reference;
- row count changed only by the intended additions;
- all catalogue songs are represented and catalogue fields agree;
- proposed rows have a phrase, intent, source, evidence date, target surface,
  proposed use, confidence, derivation type, and last-reviewed date;
- platform-native evidence names its platform;
- non-empty song/phrase/platform keys are unique;
- dates and URLs parse, multiline leakage is absent, and UTF-8 round-trips;
- a before/after diff shows only intended records.

After replacement, reopen the canonical CSV, rerun the validations, record its
SHA-256, and report added, merged, unchanged, rejected, and unavailable counts.
A saved row proves persistence, not demand or implementation.

## Outputs

For a research run, retain a dated report in the relevant release or portfolio
report location and update the CSV in the same run when the project rules
require it. Keep verified observations, calculations, interpretations,
proposals, unavailable results, and unknowns visibly distinct. If the work is a
Search Console report, follow the exact five-song and five lyric-derived section
contracts in `AGENTS.md` rather than substituting a generic summary.

## Potential problems

### Generated candidates are mistaken for search evidence

- **Symptom:** a text frequency, LLM suggestion, or similarity score is written
  as popularity, volume, competition, or ranking potential.
- **Correction:** label it by its actual discovery method and retain
  `external-demand evidence unvalidated` until a separate source supports it.
- **Verification:** every demand or trend statement points to a dated external
  metric with its source, geography or scope, unit, and limitations.

### A CSV rewrite loses its encoding or schema

- **Symptom:** headings change order, Unicode text is damaged, rows split, or a
  spreadsheet import no longer recognises the file consistently.
- **Correction:** retain the exact schema, RFC-compatible quoting, and the
  existing UTF-8 BOM; stage, parse, diff, then replace.
- **Verification:** the canonical file begins with bytes `EF BB BF`, parses to
  21 named fields, and all pre-existing unaffected rows compare equal.

### Windows PowerShell rejects `Format-Hex -Count`

- **Symptom:** Windows PowerShell 5.1 reports that `Format-Hex` has no `Count`
  parameter.
- **Cause:** Microsoft documents `-Count` as introduced in PowerShell 6.2.
- **Correction:** read only the needed prefix with
  `[System.IO.File]::ReadAllBytes($path)` and format those bytes; do not rewrite
  a correct file merely to inspect it.
- **Verification:** the current register reports the UTF-8 BOM `EF BB BF`.
- **Source checked:** Microsoft `Format-Hex` documentation, accessed 1 October
  2026: <https://learn.microsoft.com/powershell/module/microsoft.powershell.utility/format-hex>.
