# KeyBERT keyword plugin

The `keybert` keyword plugin ranks phrases from reference text using local
sentence embeddings. It implements the same `run(PluginRequest,
ExecutionContext) -> PluginResult` contract as `spacy` and `nltk`. It does not
call a generative LLM, KeyLLM, or a hosted embedding service.

KeyBERT complements rather than replaces the grammatical extractors. It scores
how closely a phrase's embedding matches its source document's embedding; it
does not itself classify proper nouns, noun chunks or named entities.

## Install

From your checkout:

```powershell
python -m pip install -e ".[keybert]"
keywordmoves plugins --kind keyword
```

The optional extra installs `keybert>=0.9,<1`,
`sentence-transformers>=3.4,<6` and `scikit-learn>=1.3,<2`, plus their
dependencies (including PyTorch). The base package
still has no runtime dependencies. Merely listing plugins neither imports
these libraries nor loads a model.

The default embedding model is
`sentence-transformers/all-MiniLM-L6-v2`, pinned to revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Its model card describes an English
sentence/short-paragraph encoder. The pin identifies the model files; it does
not guarantee identical numeric scores across hardware or library versions.

On the first model-backed extraction, Sentence Transformers may download model
files to the Hugging Face cache. It does not upload the reference text to an
inference API. `local_files_only=true` prohibits model downloads. Loading is
explicitly configured with `trust_remote_code=False` and safetensors weights;
there is no silent fallback to a different model or provider. A custom model
must be a compatible dense Sentence Transformer with a text tokenizer, a finite
`max_seq_length`, and safetensors weights. Custom code and alternative KeyBERT
backends are not enabled by this plugin.

## Basic extraction

```powershell
keywordmoves run keybert `
  --operation extract `
  --input .\reference.txt `
  --option limit=20 `
  --option min_words=1 `
  --option max_words=3 `
  --format json
```

Inline input works too:

```powershell
keywordmoves run keybert `
  --operation extract `
  --option "text=Kieran Simkin writes independent music in Brighton." `
  --format text
```

Use repeated `--input` arguments or a directory for multiple `.txt`, `.md`,
`.lrc` and `.csv` files. Files are UTF-8 (an optional BOM is accepted). CSV,
Markdown and lyrics are read as raw text, not parsed into special fields. Each
file remains a separate document; resolving the same file twice does not count
it twice. Inline text is a further document named `inline:text`.

## Selection methods

The default `method=cosine` selects the most similar candidate phrases.
`method=mmr` uses Maximal Marginal Relevance to balance similarity to the
reference against similarity to phrases already selected:

```powershell
keywordmoves run keybert --operation extract --input .\reference.txt `
  --option method=mmr --option diversity=0.7 --option limit=15
```

Higher diversity requests more varied phrases; it is not a confidence
threshold. The reported scores remain document/phrase cosine similarities,
not the intermediate MMR selection objective.

Max Sum chooses a diverse combination from a smaller similarity shortlist:

```powershell
keywordmoves run keybert --operation extract --input .\reference.txt `
  --option method=maxsum --option limit=5 --option nr_candidates=20
```

`nr_candidates` must be at least `limit`. Both are clipped to the number of
eligible phrases actually present in each document, so a short document does
not fail merely because fewer than five phrases exist. Max Sum examines
combinations: the plugin rejects more than 100,000 combinations or more than
10,000,000 combination/pair-comparison steps per document. Use MMR or reduce
the shortlist/limit when that guard is reached. There is no automatic switch
to a cheaper method.

Selection is performed separately for each document. Selected phrases are
merged, sorted by their highest **selected-document** similarity (then by
occurrence count and phrase for ties), and capped at the final `limit`.
Consequently, diversity is per document, not a global corpus-diversity promise.
Scores should not be treated as calibrated comparisons between unrelated
models, corpora or extraction plugins.

## Literal candidates and provenance

Candidates are Unicode word n-grams. Apostrophes and hyphens within words are
retained. Phrases do not cross punctuation, newlines or file boundaries. This
is deliberately more conservative than the default CountVectorizer analyser:
stopwords are not deleted and then used to invent adjacency. For example,
`paper and planes` may be a three-word candidate, but never invents the
unobserved two-word phrase `paper planes`.

Stopwords and numeric-only tokens are excluded from phrase endpoints;
stopwords inside a literal phrase are preserved (for example `City of London`).
Phrases are NFC-normalised, whitespace-normalised and case-folded for identity,
with original surface forms retained. There is no stemming or lemmatisation,
and abbreviations with internal punctuation may be split. Unusual spelling,
URLs and markup may still produce unhelpful candidates; review the output or
supply a restricted vocabulary.

Each keyword includes:

- `relationship: keybert-extracted` and `score`: cosine similarity, including
  negative scores when produced, not search demand, confidence or probability.
- `evidence`: the similarity and counted literal occurrences in the input.
- `metadata`: original surface forms (up to 20), total occurrences, document
  frequency, bounded occurrence samples with source and character offsets,
  and per-document selected ranks/scores.

Character offsets are zero-based and end-exclusive, in decoded input text
with any UTF-8 BOM removed. CRLF line endings are preserved. Occurrence counts
cover all eligible input documents, including a document where the candidate
was not selected in that document's top list. `document_scores` covers only
selections, not every possible phrase/document pairing.

Result metadata names the requested model/revision, method, library versions,
source SHA-256 hashes, source lengths and numbers of embedding chunks.
`score_aggregation` explicitly identifies the merged score's meaning. Empty
or fully filtered candidate vocabularies return a successful empty result with
an explanatory note, without loading the embedding model.

## Rank a restricted vocabulary

Repeated `--keyword` arguments restrict candidate generation to those phrases;
they do not add absent phrases or serve as steering prompts:

```powershell
keywordmoves run keybert --operation extract --input .\reference.txt `
  --keyword "paper planes" --keyword "independent music" --keyword "Brighton" `
  --option max_words=3
```

Candidate length, stopword and frequency filters still apply. To re-rank
**literal** spaCy or NLTK results in Python:

```python
from pathlib import Path
from keywordmoves import PluginRegistry, PluginRequest
from keywordmoves.models import ExecutionContext

plugins = PluginRegistry()
context = ExecutionContext(llms=None)
inputs = (Path("reference.txt"),)
linguistic = plugins.get("nltk").run(
    PluginRequest(operation="extract", inputs=inputs, options={
        "features": "proper-nouns,noun-chunks", "lemmatize": False, "limit": 100,
    }),
    context,
)
if not linguistic.keywords:
    raise ValueError("No linguistic candidates to rank.")
ranked = plugins.get("keybert").run(
    PluginRequest(operation="extract", inputs=inputs,
                  keywords=tuple(item.phrase for item in linguistic.keywords),
                  options={"max_words": 8, "stop_words": "none", "limit": 20}),
    context,
)
print(ranked.to_dict())
```

Each prerequisite plugin needs its own optional dependencies and models/data.
Disable lemmatisation for this hand-off, since the KeyBERT filter requires
phrases that occur literally. Differences in tokenisation or punctuation can
still exclude some linguistic candidates. It does not copy POS/entity labels
from the previous result or claim to have rediscovered those labels.

## Models, cache and offline use

Use `--option keybert_model=MODEL_OR_LOCAL_DIRECTORY` to change the encoder.
The CLI's `--model` and `--llm` flags remain reserved for generative LLM plugins.
A different model does not inherit the default model's revision; specify
`--option revision=COMMIT` to pin it. An unpinned custom model is reported with
a null requested revision, not described as reproducible.

To prefetch the pinned default while online (PowerShell):

```powershell
python -c "from sentence_transformers import SentenceTransformer; from keywordmoves.builtin.keybert_keywords import DEFAULT_MODEL, DEFAULT_REVISION; SentenceTransformer(DEFAULT_MODEL, revision=DEFAULT_REVISION, cache_folder='./model-cache', trust_remote_code=False, model_kwargs={'use_safetensors': True})"
```

Then run using only the cache:

```powershell
keywordmoves run keybert --operation extract --input .\reference.txt `
  --option cache_dir=./model-cache --option local_files_only=true
```

The default device is CPU. `device=auto`, `cuda`, `cuda:0` and `mps` are also
accepted; hardware/backend availability is checked by the underlying encoder.
A failed explicit device does not silently become CPU. Missing models,
unsupported safetensors layouts and incompatible local installations produce
configuration errors with the normal CLI exit code 2.

## Long text and resource limits

Document embeddings are computed from tokenizer-sized chunks rather than
silently embedding only the first part of a long reference. Splits occur at
whitespace-delimited word boundaries. Every chunk fits the encoder's actual
token budget, including special tokens. Normalised chunk vectors are pooled
using content-token-count weights and normalised again. This is an explicit
adaptation of native whole-document KeyBERT, not an assertion that the model
understands unlimited context. Cross-chunk semantic interactions are lost.

A single overlong whitespace-delimited token or candidate phrase raises an
error rather than being silently truncated. Whitespace itself does not
contribute tokens. A document without eligible candidates is not embedded.
Candidate embeddings are computed once per extraction run, then reused for
all documents. At most one loaded encoder/runtime is cached per plugin
instance; changing model, revision, cache, device or offline mode replaces it.

`max_candidates` limits the unique candidate vocabulary **before** frequency
filtering, preventing unbounded embedding/pairwise work. `max_chars` bounds the
combined decoded text for the whole run. These are guardrails, not a guarantee
of a fixed peak memory use; use smaller inputs when hardware is constrained.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `text` | Not set | Inline reference text, in addition to files. |
| `keybert_model` | Pinned MiniLM model above | Encoder name or local directory. |
| `revision` | Pin for default; otherwise null | Requested model revision. |
| `cache_dir` | Library default | Model cache location. |
| `local_files_only` | `false` | Never download model files when true. |
| `device` | `cpu` | CPU, auto, CUDA device or MPS. |
| `method` | `cosine` | `cosine`, `mmr` or `maxsum`. |
| `diversity` | `0.5` | 0–1; explicitly setting it requires `method=mmr`. |
| `nr_candidates` | `20` | Max Sum shortlist; explicitly setting it requires `method=maxsum`. |
| `limit` | `20` | Per-document selection and final merged cap, 1–1000. |
| `min_words`, `max_words` | `1`, `3` | Word n-gram lengths, 1–10. |
| `min_length` | `2` | Minimum normalised phrase length in characters. |
| `stop_words` | `english` | scikit-learn English list, or `none`. |
| `stopwords` | Empty | Additional comma-separated stopwords, or a Python sequence. |
| `min_occurrences` | `1` | Minimum total literal occurrence count. |
| `min_df` | `1` | Minimum number of input documents containing the phrase. |
| `max_candidates` | `2000` | Unique candidate guard, up to 5000. |
| `max_chars` | `1000000` | Combined decoded-text character guard, up to 10000000. |
| `max_occurrences` | `10` | Stored source-span samples per keyword; 0 disables samples, not counts. |
| `batch_size` | `32` | Encoder batch size, 1–256. |

There is no `features` option: this plugin is semantic ranking, not a noun/NER
tagger. Unknown options are rejected rather than silently ignored.

## Tests

```powershell
python -m pip install -e ".[dev,keybert]"
python -m pytest tests/test_keybert_config.py tests/test_keybert_keywords.py
```

Configuration tests need no embedding model. Adapter tests use deterministic
controlled embeddings and real scikit-learn vectorisers; the API tests execute
real KeyBERT cosine/MMR/Max Sum with those controlled embeddings. The optional
pretrained smoke test uses only a cached model unless explicitly required:

```powershell
$env:KEYWORDMOVES_REQUIRE_KEYBERT_MODEL = "1"
python -m pytest tests/test_keybert_model.py
```

That setting permits the pinned model download and fails instead of skipping
when it cannot load. CI exercises the library API in its normal matrix and
runs this model-backed test in separate Linux and Windows jobs. The smoke test
checks integration and traceable outputs, not a quantitative keyword-quality
benchmark.

## Sources

Implementation contracts checked 2 October 2026:

- [KeyBERT source and public API](https://github.com/MaartenGr/KeyBERT/blob/master/keybert/_model.py)
- [KeyBERT quickstart](https://maartengr.github.io/KeyBERT/guides/quickstart.html)
- [MMR implementation](https://maartengr.github.io/KeyBERT/api/mmr.html)
- [Max Sum implementation](https://maartengr.github.io/KeyBERT/api/maxsum.html)
- [SentenceTransformer API](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
- [MiniLM model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
- [Model revision metadata](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2)
