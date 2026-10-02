# spaCy keyword extraction

The `spacy` **keyword plugin** generates candidates from supplied reference text.
It implements `run(PluginRequest, ExecutionContext)` and never selects an LLM.
The existing OpenAI, Hugging Face, text-library and evidence-import plugins are
unchanged. No release version is changed by this addition.

## Install

From the repository root:

```powershell
python -m pip install -e ".[spacy]"
python -m spacy download en_core_web_sm
keywordmoves plugins --kind keyword
```

The optional extra requires `spacy>=3.8.11,<4`. Install the pipeline separately:
spaCy's download command selects a compatible package. The default English
pipeline is `en_core_web_sm`. Other compatible installed pipelines or trusted
local model directories can be selected with `--option spacy_model=NAME`.
Do **not** use `--model`: that existing CLI flag selects an LLM model.

There is no automatic installation, download or fallback. A core-only install
can still list all plugins without importing spaCy. A missing dependency or
pipeline produces an actionable configuration error and CLI exit status 2.

The default English pipeline runs locally after installation, without an API
key. The plugin has no hosted API client and does not call `context.llms`.
Loading a custom pipeline can execute its components' Python code: use trusted
local pipelines, not network-backed custom components, for offline/private work.

## Reference text

Read a UTF-8 reference file:

```powershell
keywordmoves run spacy `
  --operation extract `
  --input .\reference.txt `
  --option limit=50 `
  --format json
```

Or supply the text directly:

```powershell
keywordmoves run spacy `
  --operation extract `
  --option "text=Kieran Simkin writes paper planes in Brighton. Paper planes inspire independent music." `
  --format text
```

Equivalent Bash command:

```bash
keywordmoves run spacy --operation extract \
  --input ./reference.txt --option limit=50 --format json
```

`--input` is repeatable and may refer to a directory. Supported file suffixes
are `.txt`, `.md`, `.lrc` and `.csv` (case-insensitive); directories are searched
recursively in sorted order. Duplicate resolved file paths are read once.
Files are read as plain text, not parsed as Markdown, LRC or CSV fields. For
cleaner results, export just the reference prose or lyrics rather than labels,
timestamps or table headers. A UTF-8 BOM is accepted.

Inline text and files can be combined. Every input is processed as a separate
spaCy document, so names and phrases cannot bridge files. Multi-line candidate
spans are discarded rather than inventing phrases across lyric lines. Empty
files among valid inputs are explicitly noted; entirely empty input is an
error. `--keyword` is not a substitute for reference text.

## What is extracted

| Feature | Default | Behaviour |
| --- | --- | --- |
| `proper-nouns` | On | Every `PROPN` token and contiguous multi-token names, including hyphenated names. Uses POS annotations, not capitalization alone. Names retain their spelling; separate named-entity spans are not joined into a single proper name. |
| `noun-chunks` | On | Actual `Doc.noun_chunks` from spaCy's dependency parse. Leading determiners/possessive pronouns and edge punctuation are removed. Pronoun-only chunks are excluded. |
| `entities` | On | Named entities such as people, organisations, places, products, events and works of art; the actual spaCy entity labels are retained. |
| `keyphrases` | On | Contiguous adjective/noun/proper-noun patterns ending in a noun: e.g. `independent music` or `paper plane`. These POS-pattern candidates are labelled separately from parser-derived noun chunks. |
| `nouns` | On | Useful standalone common nouns, optionally lemmatised. |
| `adjectives` | Off | Standalone descriptive words, useful when a topic's mood or style matters. |
| `verbs` | Off | Standalone lexical verbs, useful for actions; auxiliary verbs are not selected. |

For the example reference text, intended candidates include `Kieran Simkin`,
`Brighton`, `paper plane` and `independent music`. Actual predictions depend on
the installed pipeline and its training data; these are examples, not a
promise of exact model output. Statistical NER can miss unusual artist/song
names, and lyric syntax can confuse POS tagging and dependency parsing.

Only proper nouns and noun chunks:

```powershell
keywordmoves run spacy --operation extract --input .\reference.txt `
  --option "features=proper-nouns,noun-chunks"
```

Include standalone adjectives and verbs as well as the defaults:

```powershell
keywordmoves run spacy --operation extract --input .\reference.txt `
  --option "features=proper-nouns,noun-chunks,entities,keyphrases,nouns,adjectives,verbs"
```

`features` **replaces**, rather than extends, the default feature set.
POS-based features require complete POS annotations. Noun chunks additionally
require a dependency parse and a language with a noun-chunk iterator. Entities
require `ENT_IOB` annotations. Missing annotations are errors, not silent
empty results. A custom POS-only pipeline can be used by explicitly selecting
features such as `proper-nouns,nouns` that it supports.

## Filtering and normalisation

Common noun/adjective/verb inflections are lemmatised by default, so detected
`plane` and `planes` occurrences can merge. Named entities and spans containing
proper nouns or entity tokens retain their forms: a title such as `Arcadians`
is not singularised to `Arcadian`. Missing lemmas retain the original token
form with an explicit note. Use `lemmatize=false` for surface-form extraction.
Deduplication is case-insensitive and Unicode NFC-normalised; different aliases
or abbreviations are **not** resolved to the same real-world entity.

Stopword-only candidates, punctuation, URLs, email addresses and purely numeric
tokens are suppressed. Exact extra stopwords/phrases can be passed with
`stopwords`. Content-token checks also suppress unwanted standalone words;
interior words are never deleted to create a phrase that was not contiguous in
the source. Proper names can legitimately contain built-in stopwords (for
example `The Who`). Exact user-specified stop phrases still exclude them.

By default the labels `DATE`, `TIME`, `PERCENT`, `MONEY`, `QUANTITY`, `ORDINAL`
and `CARDINAL` are excluded. Candidate spans touching those labelled tokens are
also excluded from other extraction paths. Use `entity_labels=*` to allow those
labels, or a comma-separated explicit selection such as `PERSON,ORG,GPE`.
Numeric-only candidates remain filtered even with `*`. An explicit label list
selects the **entity** extraction path; proper nouns of other non-numeric types
can still be returned when the proper-nouns feature is enabled.

## Options

Pass each option with `--option KEY=VALUE`. Python callers use the same keys in
`PluginRequest.options`. Unknown options and invalid values are rejected.

| Option | Default | Meaning |
| --- | --- | --- |
| `text` | None | Inline reference text; alternatively/additionally use `--input`. |
| `spacy_model` | `en_core_web_sm` | Installed pipeline package or trusted local directory. |
| `features` | First five features above | Comma-separated feature selection. |
| `entity_labels` | All except numeric/date labels above | Explicit labels, or `*` to include all labels. |
| `lemmatize` | `true` | Use available lemmas for ordinary words; never singularise names. |
| `stopwords` | Empty | Extra comma-separated unwanted words/exact phrases. |
| `limit` | `50` | Maximum returned candidates; 1–10,000. |
| `min_occurrences` | `1` | Minimum number of distinct detected source spans; 1–1,000,000. |
| `min_length` | `2` | Minimum surface length in characters; 1–200. Use 1 for single-letter names. |
| `max_words` | `8` | Maximum non-punctuation token count per phrase; 1–50. |
| `max_chars` | `1000000` | Maximum decoded characters **per input**; 1–10,000,000. Oversize inputs fail rather than truncate. Split large inputs or explicitly raise the limit. |
| `max_occurrences` | `10` | Maximum positional records retained per candidate; 0–1,000. Counts still cover all detected occurrences. |
| `batch_size` | `16` | Batch size for `nlp.pipe`; 1–256. Inputs remain separate documents. |

Example filter combination:

```powershell
keywordmoves run spacy --operation extract --input .\reference.txt `
  --option min_occurrences=2 `
  --option max_words=5 `
  --option "stopwords=thing,stuff,paper plane" `
  --option lemmatize=false
```

## Evidence, scores and source positions

Each candidate uses `relationship: spacy-extracted`. It includes:

- All matching `features`, entity labels, observed surface forms and document
  frequency. Surface forms retain spelling/case with whitespace normalised.
- `detected_occurrences` evidence: unique `(source, start_char, end_char)` spans.
  An occurrence detected as an entity, proper noun and noun chunk counts once,
  not three times. This counts detections, not every possible literal mention.
- Bounded positional records with all detectors for that occurrence, plus
  `occurrences_omitted` when the output cap is reached.

Offsets are zero-based Python Unicode-character offsets with an exclusive end,
relative to that source's decoded input. Files have their UTF-8 BOM removed and
CRLF/CR newlines normalised to LF by the text reader; these are **not byte offsets**.
Inline text is used as supplied. Input metadata records a SHA-256 of the exact
UTF-8-encoded decoded text, its character/token counts, and its source path.
The selected model, model version, spaCy version, language and pipeline names
are also recorded. No complete reference document is copied into the output,
but candidate text, names, surface forms and paths can themselves be sensitive.
`max_occurrences=0` suppresses positional records; it does not anonymise output.

Ranking is deliberately a documented heuristic, not a trained keyword-quality
score or a source of search-demand evidence:

```text
raw = highest_feature_weight * (1 + log2(detected_occurrences))
      + 0.1 * min(word_count, 5)
score = raw / maximum_raw_among_eligible_candidates
```

Feature weights are 4 for entities/proper nouns, 3 for noun chunks, 2 for
POS-pattern keyphrases, 1 for standalone nouns, and 0.5 for adjectives/verbs.
A span's overlapping detectors do not add their weights. Ties are resolved by
word count, then case-insensitive phrase. The weights/formula and raw salience
are present in metadata. Scores are relative to this run, not probabilities,
confidence estimates, search volume or comparable cross-corpus measurements.
Use the evidence-import plugins separately to research demand.

## Python API

```python
from keywordmoves import PluginRegistry, PluginRequest
from keywordmoves.models import ExecutionContext

result = PluginRegistry().get("spacy").run(
    PluginRequest(
        operation="extract",
        options={
            "text": "Kieran Simkin writes paper planes in Brighton.",
            "spacy_model": "en_core_web_sm",
            "features": ["proper-nouns", "noun-chunks", "entities"],
            "limit": 30,
        },
    ),
    ExecutionContext(llms=None),
)
for candidate in result.keywords:
    print(candidate.phrase, candidate.metadata["features"])
```

## Tests

```powershell
python -m pip install -e ".[dev,spacy]"
python -m pytest -p no:cacheprovider --basetemp=.tmp\pytest-spacy
```

`--basetemp` is **cleared by pytest**: use a dedicated disposable directory.
Core configuration/input/lazy-import tests need no spaCy installation. Tests
with spaCy installed use its actual `Doc`, `Span` and noun-chunk implementations
with hand-annotated text, without downloading a model. They check exact
extraction and offset behaviour; they are not a measure of statistical accuracy.

The trained-model smoke test is separate and normally skips when
`en_core_web_sm` is not installed. To run it:

```powershell
python -m spacy download en_core_web_sm
python -m pytest tests/test_spacy_model.py
```

CI runs the existing Python-version matrix with the spaCy extra, plus a
separate trained-model job. That job sets `KEYWORDMOVES_REQUIRE_SPACY_MODEL=1`
so a missing model is a failure rather than a silent skip.

## Primary references

Implementation checked against spaCy's official documentation on 2 October 2026:

- [Linguistic features: POS, noun chunks, entities and lemmas](https://spacy.io/usage/linguistic-features)
- [Doc API: annotations, noun chunks, entities and offsets](https://spacy.io/api/doc)
- [Token API: POS, lemmas, stopwords and lexical flags](https://spacy.io/api/token)
- [Installing and loading trained pipelines](https://spacy.io/usage/models)
- [English model descriptions and limitations](https://spacy.io/models/en)
