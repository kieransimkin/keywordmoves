# NLTK keyword extraction

`nltk` is a keyword plugin, not an LLM plugin. It implements
`run(PluginRequest, ExecutionContext) -> PluginResult` and never uses the LLM
registry. It extracts candidates found in supplied English reference text,
without calling an API or automatically downloading models or corpora.

## Installation

From the repository root:

```powershell
python -m pip install -e ".[nltk]"
python -m nltk.downloader punkt_tab averaged_perceptron_tagger_eng maxent_ne_chunker_tab words stopwords wordnet
keywordmoves plugins --kind keyword
```

The optional dependency is `nltk>=3.9.2,<4`. The core remains dependency-free,
and importing KeywordMoves or listing plugins does not import NLTK. NLTK data
is installed separately and can change independently of the Python library.
The plugin records library version and resource identifiers; these are **not**
checksums or pinned versions of the downloaded data. Archive your approved
NLTK data directory when exact reproducibility matters.

The selected features determine which data is required:

| Resource | Used for |
| --- | --- |
| `punkt_tab` | Pretrained English sentence boundaries, within each input line. |
| `averaged_perceptron_tagger_eng` | English Penn Treebank part-of-speech tags. |
| `stopwords` | English stopword filtering. |
| `maxent_ne_chunker_tab`, `words` | Named entities, only when `entities` is enabled. |
| `wordnet` | Common-word lemmatisation, only when `lemmatize=true`. |

For names and noun phrases without NER or lemmatisation, a smaller setup is:

```powershell
python -m nltk.downloader punkt_tab averaged_perceptron_tagger_eng stopwords
keywordmoves run nltk --operation extract --input .\reference.txt --option "features=proper-nouns,noun-chunks" --option lemmatize=false
```

Missing resources cause an actionable error containing an explicit installation
command. A failed selected feature is not silently skipped, substituted with
another model, or replaced with a download. Use the modern resource names
above rather than the legacy `punkt`, `averaged_perceptron_tagger`, or
`maxent_ne_chunker` packages.

### Custom data directory or offline use

In PowerShell, provision data while connected, then set the directory **before**
starting KeywordMoves:

```powershell
python -m nltk.downloader -d C:\Models\nltk_data punkt_tab averaged_perceptron_tagger_eng maxent_ne_chunker_tab words stopwords wordnet
$env:NLTK_DATA = "C:\Models\nltk_data"
keywordmoves run nltk --operation extract --input .\reference.txt
```

In Bash/zsh the equivalent variable assignment is:

```bash
export NLTK_DATA="/path/to/nltk_data"
```

An offline machine can use a preprovisioned, trusted data directory. The plugin
uses NLTK's normal resource discovery without modifying its global search path.
It does not load an arbitrary user-specified pickle or custom model file.
There is no API key, `--llm`, `--model`, or `nltk_model` option for this plugin.

## Use

File input:

```powershell
keywordmoves run nltk `
  --operation extract `
  --input .\reference.txt `
  --option limit=50 `
  --format json
```

Inline reference text:

```powershell
keywordmoves run nltk --operation extract --option "text=Kieran Simkin writes independent music in Brighton." --format text
```

Multiple documents:

```powershell
keywordmoves run nltk --operation extract --input .\lyrics.txt --input .\notes.md --input .\references
```

Select only the originally requested features:

```powershell
keywordmoves run nltk --operation extract --input .\reference.txt --option "features=proper-nouns,noun-chunks"
```

Enable standalone adjectives and verbs as well as the default features:

```powershell
keywordmoves run nltk --operation extract --input .\reference.txt --option "features=proper-nouns,noun-chunks,entities,keyphrases,nouns,adjectives,verbs"
```

Use native NLTK entity labels, for example:

```powershell
keywordmoves run nltk --operation extract --input .\reference.txt --option features=entities --option "entity_labels=PERSON,ORGANIZATION,GPE"
```

`ORGANIZATION` is an NLTK label, not spaCy's `ORG`. Entity-label filtering only
affects the `entities` detector; other enabled detectors may still emit a name.
`--keyword` is rejected because this plugin requires reference text, not seeds.

## Detectors and normalisation

The default features are `proper-nouns`, `noun-chunks`, `entities`, `keyphrases`
and `nouns`. Their labels are deliberately shared with the spaCy extractor.

| Feature | Implementation |
| --- | --- |
| `proper-nouns` | Individual `NNP`/`NNPS` tokens and contiguous multi-token names, including hyphen connectors. Known adjacent entity boundaries are respected when NER is active. |
| `noun-chunks` | NLTK `RegexpParser` noun phrases, with leading determiners and possessive pronouns removed. |
| `entities` | NLTK's multiclass maximum-entropy named-entity chunker; preserves native labels. |
| `keyphrases` | Contiguous adjective/noun sequences ending in a noun, bounded by `max_words`. No stopword removal that invents adjacency. |
| `nouns` | Common nouns with tags `NN` or `NNS`. |
| `adjectives` | Optional standalone `JJ`, `JJR`, `JJS` candidates. |
| `verbs` | Optional standalone `VB*` candidates; no fabricated verb phrases. |

The noun grammar is recorded verbatim in result metadata:

```text
NP: {<DT|PRP\$>?<JJ.*>*<NN.*>+}
```

This is a **shallow POS-based grammar**, not a dependency parser or a trained
noun-chunk model. It will not reliably retain nested phrases or connectors such
as "of" inside noun chunks. The entity detector can retain recognised names
such as "University of Oxford" including their connectors.

By default, ordinary nouns, adjectives and verbs are lowercased and passed to
WordNet's lemmatiser with their appropriate part of speech. For example, tagged
common nouns "planes" and "plane" can merge. Spans containing tagged proper
nouns or recognised entity tokens retain their surface text rather than being
singularised. Unrecognised or mistagged names cannot be guaranteed protection.
This plugin does not add WordNet synonyms, synonyms from a thesaurus, or
unstated concepts; WordNet is used for lemmatisation only.

Whitespace is collapsed and Unicode NFC is used for candidate display and
case-insensitive deduplication. Original surface forms are retained in metadata.
URLs/emails are blocked across their constituent tokenizer spans, including
fragments. Standalone numbers and stopwords are excluded; numbers inside a
recognised name can remain. Custom stopwords exclude exact candidate phrases
case-insensitively and participate in common-word filtering, not substring
matching or deletion from the original reference text.

## Options

All options use `--option KEY=VALUE`, without an `llm_` prefix. Unknown options
are errors rather than ignored settings.

| Option | Default | Meaning |
| --- | --- | --- |
| `text` | None | Inline reference text; can accompany file input. |
| `language` | `english` | Only English is supported by this implementation. |
| `features` | Five default detectors above | Comma-separated selection; specifying it replaces the default selection. |
| `entity_labels` | All native labels | Comma-separated case-sensitive NLTK labels, or `*`. |
| `stopwords` | Empty | Extra case-insensitive words or complete phrases to exclude. |
| `lemmatize` | `true` | WordNet lemmatisation of ordinary words; explicit `true` or `false`. |
| `limit` | `50` | Maximum returned candidates, 1-10,000. |
| `min_occurrences` | `1` | Minimum distinct detected spans, 1-1,000,000. |
| `min_length` | `2` | Minimum surface character length, 1-200. |
| `max_words` | `8` | Maximum lexical tokens per candidate, 1-50. |
| `max_chars` | `1000000` | Per-document character limit, 1-10,000,000. Oversize input is rejected, never silently truncated. |
| `max_occurrences` | `10` | Retained location examples per candidate, 0-1,000. Does not reduce counts. |
| `batch_size` | `16` | Sentences sent to the POS tagger per batch, 1-256. Does not join sentences. |

Python callers use the same keys in `PluginRequest.options`; `features`,
`entity_labels` and `stopwords` also accept sequences of strings.

```python
from keywordmoves import PluginRegistry, PluginRequest
from keywordmoves.models import ExecutionContext

result = PluginRegistry().get("nltk").run(
    PluginRequest(
        operation="extract",
        options={"text": "The bright paper planes fly.", "limit": 20},
    ),
    ExecutionContext(llms=None),
)
print(result.to_dict())
```

## Evidence, offsets and ranking

Each candidate has `relationship="nltk-extracted"`, detector labels,
entity labels, surface forms, a `detected_occurrences` count and
`document_frequency`. Identical `(source, start_char, end_char)` detections
from multiple features count **once**; their feature labels are combined.
A nested shorter phrase is a separate candidate, not a duplicate detection of
the longer name. Counts refer to detected spans, not a separate exhaustive
string search or search-engine demand.

Occurrence offsets are zero-based, end-exclusive Python character offsets in
the UTF-8-decoded reference text after an optional initial BOM is removed.
CRLF is retained. Offsets are not UTF-8 byte offsets or JavaScript UTF-16 indices.
Source metadata includes character/token counts and a SHA-256 of the decoded
text. Stored occurrences are capped separately from total counts; the number
omitted is reported. There is no separate full-input-text field, but extracted phrases can reproduce
an entire short input. Names, source paths and phrase locations may be sensitive.

Ranking matches the spaCy plugin's transparent heuristic:

```text
raw = maximum feature weight * (1 + log2(distinct detected occurrences))
      + 0.1 * min(word count, 5)
score = raw / maximum raw score among eligible candidates
```

Feature weights: entities/proper nouns 4, noun chunks 3, keyphrases 2, nouns 1,
adjectives/verbs 0.5. Stable tie-breaking uses word count and canonical phrase.
These are policy choices for ordering text candidates, **not** model confidence,
search popularity, ranking potential, or evidence of demand. They are not
calibrated for comparison across unrelated texts or different extractors.

## Input boundaries and limitations

Each `.txt`, `.md`, `.lrc` or `.csv` file is a separate document. Directories
are traversed in sorted order and resolved file paths are deduplicated. Text is
not concatenated across documents. Newlines are hard boundaries (helpful for
lyrics), and the pretrained sentence tokenizer splits within each line. This
can miss phrases wrapped over multiple lines in prose; it is deliberate rather
than silently constructing cross-line phrases.

Files are treated as raw text: this does not parse Markdown, remove LRC timing
codes, or select CSV columns. Empty files are ignored for extraction but retain
source metadata when another non-empty document is present. English POS/NER
models can misclassify unfamiliar names, capitalised titles, lyric fragments
and specialist terminology. Different results from spaCy are expected and
should be reviewed, not treated as independent confirmation of search demand.

## Tests

```powershell
python -m pip install -e ".[dev,nltk]"
python -m pytest tests/test_nltk_config.py tests/test_nltk_keywords.py
```

Configuration tests run without NLTK. API tests use real NLTK tokenisers,
`UnigramTagger` and `RegexpParser`, with controlled entity and lemma fixtures;
they need no external data. They test feature labels, boundaries, unique counts,
input errors, offset integrity, runtime loading, dependency isolation and CLI
integration. They do not measure pretrained accuracy.

The optional pretrained test uses the actual English models and WordNet:

```powershell
python -m nltk.downloader -q -e punkt_tab averaged_perceptron_tagger_eng maxent_ne_chunker_tab words stopwords wordnet
$env:KEYWORDMOVES_REQUIRE_NLTK_DATA = "1"
python -m pytest tests/test_nltk_model.py
```

CI runs API tests in the normal Python-version matrix, plus mandatory
pretrained-data tests on Linux and Windows. Without the environment flag,
missing resources skip the optional smoke test; with the flag they fail.
For restricted Windows workspaces, pytest's `--basetemp` can point to a dedicated
repository-local temporary directory; pytest clears that directory on each run.

## References checked

Official NLTK documentation, checked 2 October 2026:

- Data installation and `NLTK_DATA`: <https://www.nltk.org/data.html>
- POS tagging: <https://www.nltk.org/api/nltk.tag.html>
- Grammar chunker: <https://www.nltk.org/api/nltk.chunk.RegexpParser.html>
- Named-entity chunking: <https://www.nltk.org/api/nltk.chunk.html>
- Treebank tokenisation and spans: <https://www.nltk.org/api/nltk.tokenize.TreebankWordTokenizer.html>
- WordNet lemmatisation: <https://www.nltk.org/api/nltk.stem.wordnet.html>
- Release notes and modern non-pickle resource support: <https://www.nltk.org/news.html>
