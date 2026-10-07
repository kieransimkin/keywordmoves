# KeywordMoves

KeywordMoves is the keyword-discovery and search-evidence layer in Kieran
Simkin's DanceFlow ecosystem. It provides small, composable Python plugins for
generating keywords, finding related language, and attaching clearly labelled
evidence such as relative Google Trends interest.

It keeps two plugin systems deliberately separate:

- `keywordmoves.plugins` contains keyword sources and analysers.
- `keywordmoves.llms` contains interchangeable LLM runtimes.

A keyword plugin that needs generative LLM inference must ask for an LLM plugin
by name. It must not import or silently select a model provider of its own.

## Current plugins

| Plugin | Kind | What it does |
| --- | --- | --- |
| `google-search` | keyword | Google Search Console, Trends exports, search observations and page audits with source-specific metrics. |
| `bing-search` | keyword | Bing Webmaster, reviewed search results and keyword planning evidence. |
| `youtube`, `tiktok`, `instagram`, `reddit` | keyword | Separate platform workbenches for authorised exports, native observations, local reference text and supported providers. Read each module's access and metric limits. |
| `google-trends` | keyword | Imports Google Trends interest and related-query CSV exports, preserving the distinction between relative index values and absolute search volume. |
| `keybert` | keyword | Ranks literal reference-text phrases with local sentence embeddings; supports cosine similarity, MMR and Max Sum selection. No generative LLM required. |
| `nltk` | keyword | Extracts proper nouns, grammar-based noun chunks, named entities and keyphrases locally with NLTK. No LLM required. |
| `observed-evidence` | keyword | Validates and imports dated browser, account and public-tool observations without scraping authenticated or private interfaces. |
| `spacy` | keyword | Extracts proper nouns, noun chunks, named entities and useful noun/adjective phrases from reference text with a local spaCy pipeline. No LLM required. |
| `text-library` | keyword | Extracts contiguous Unicode phrases and source spans with `extract-literal`, retains legacy local associations, or asks an explicitly selected LLM for semantic proposals. |
| `huggingface-transformers` | LLM | Runs a local Hugging Face seq2seq or causal instruction model through PyTorch. Its default is the Apache-2.0 `Qwen/Qwen2.5-0.5B-Instruct`, pinned to a reviewed model revision. |
| `openai` | LLM | Uses OpenAI-hosted text models through the Responses API. Accepts `OPENAI_API_KEY` or `--openai-api-key`; the model is selectable with `--model`. |

Google's official Trends API is currently an access-controlled alpha. The
built-in plugin therefore supports reproducible CSV exports now rather than
depending on the archived, unofficial `pytrends` scraper or guessing an API
contract. A live official-API backend can be added when access and its exact
contract are available.

## Install

The core and Google Trends importer have no runtime dependencies:

```powershell
python -m pip install -e .
```

Install the local PyTorch/Hugging Face runtime when it is needed:

```powershell
python -m pip install -e ".[huggingface]"
```

The first Hugging Face model-backed run downloads the selected model unless
`llm_local_files_only=true` is supplied. Model files stay in the configured
Hugging Face cache; the input text is processed locally and is not sent to a
hosted API.

The default Qwen checkpoint is pinned to revision
`2b01de6d1108f9b2b5e46a726aa678a359b6c03b`. KeywordMoves records the selected
model and revision in result metadata. A different Hugging Face model remains
selectable with `--model` and `--option llm_revision=<commit>`.

Install the optional OpenAI runtime separately (no PyTorch required):

```powershell
python -m pip install -e ".[openai]"
```

Unlike the local Hugging Face runtime, selecting `--llm openai` sends the
supplied prompts to OpenAI's hosted API. API usage may incur charges.

Install the independent spaCy keyword extractor and its English pipeline:

```powershell
python -m pip install -e ".[spacy]"
python -m spacy download en_core_web_sm
```

The pipeline download is an explicit setup step, not something KeywordMoves
performs automatically. Subsequent extraction with this pipeline is local.

Install the alternative NLTK extractor and the data used by its default features:

```powershell
python -m pip install -e ".[nltk]"
python -m nltk.downloader punkt_tab averaged_perceptron_tagger_eng maxent_ne_chunker_tab words stopwords wordnet
```

This is also local after setup. KeywordMoves never downloads NLTK data implicitly.
For offline/custom data directories and smaller installs, see [docs/nltk.md](docs/nltk.md).

Install the independent KeyBERT semantic keyword extractor:

```powershell
python -m pip install -e ".[keybert]"
```

Its first model-backed run may download the pinned Sentence Transformers model.
Text is embedded locally, not sent to a hosted inference API. For prefetching
and strictly offline use, see [docs/keybert.md](docs/keybert.md).

## Use

List both plugin layers:

```powershell
keywordmoves plugins
```

Import a Google Trends interest-over-time export:

```powershell
keywordmoves run google-trends `
  --operation import-interest `
  --input .\multiTimeline.csv `
  --option geography=GB `
  --option observed_at=2026-09-30
```

Extract and expand ideas from a lyrics file with an explicit LLM selection:

```powershell
keywordmoves run text-library `
  --operation extract `
  --input .\lyrics.txt `
  --llm huggingface-transformers `
  --model Qwen/Qwen2.5-0.5B-Instruct `
  --option limit=20
```

Use the transparent local extractor without any LLM:

```powershell
keywordmoves run text-library --operation extract-local --input .\lyrics.txt
```

Import a reviewed JSON observation file for Search Console, native platform
search, autocomplete or a public keyword tool:

```powershell
keywordmoves run observed-evidence `
  --operation import-observations `
  --input .\keyword-observations.json `
  --format json
```

The file must use `keywordmoves-observations/v1`. Each observation requires a
phrase, source, actual metric, observation date and platform. Record blocked,
empty or unreadable surfaces with `availability: unavailable`; do not turn an
unavailable check into a zero-demand claim. This importer is the supported
route for browser-only and authenticated evidence. It deliberately does not
scrape sites or call private endpoints.

LLM suggestions are labelled as proposals. They do not establish popularity,
search volume, competition, ranking potential, or suitability on a particular
platform.

### Extract proper nouns and noun chunks with spaCy

```powershell
keywordmoves run spacy `
  --operation extract `
  --input .\reference.txt `
  --option limit=50
```

This also extracts named entities, common nouns and contiguous adjective/noun
keyphrases. To focus only on the requested noun categories, add:

```powershell
--option "features=proper-nouns,noun-chunks"
```

Use `--option "text=Your reference text"` for inline input, or repeat `--input`
for multiple files/directories. Select another installed spaCy pipeline with
`--option spacy_model=NAME`, **not** the LLM-specific `--model` flag.

Results preserve detector labels, entity types, original surface forms,
deduplicated occurrence counts and source character offsets. Common-word
inflections can be merged without singularising names. Scores describe only
heuristic salience in the reference text, not search demand or model confidence.
See [docs/spacy.md](docs/spacy.md) for all options, examples and limitations.

### Extract with NLTK

```powershell
keywordmoves run nltk `
  --operation extract `
  --input .\reference.txt `
  --option limit=50
```

The `nltk` and `spacy` extractors share feature names and candidate metadata.
Use `--option "features=proper-nouns,noun-chunks"` to focus on names and noun
phrases, or `--option "text=Your reference text"` for inline input. NLTK noun
chunks use a part-of-speech grammar rather than spaCy's dependency parser, so
results need not agree. This initial NLTK implementation is English-only.
Names retain their surface forms; ordinary words can be lemmatised with WordNet.
See [docs/nltk.md](docs/nltk.md) for all options, resource setup and limitations.

### Rank reference-text phrases with KeyBERT

```powershell
keywordmoves run keybert `
  --operation extract `
  --input .\reference.txt `
  --option limit=20 `
  --option max_words=3 `
  --option method=mmr `
  --option diversity=0.7
```

KeyBERT uses embedding similarity, not POS or entity labels. Its default
selection is `method=cosine`; `mmr` and `maxsum` select more varied phrases.
Use repeated `--keyword "phrase"` arguments to restrict the vocabulary, or
`--option "text=Reference text"` for inline input. Select another encoder with
`--option keybert_model=NAME`, not the LLM-specific `--model` switch.

Candidates retain literal source spans; long references are embedded in
bounded chunks rather than silently truncated. Scores are not search demand
or confidence. See [docs/keybert.md](docs/keybert.md) for model caching,
resource limits, all options and an NLTK/spaCy-to-KeyBERT re-ranking example.

### Use OpenAI models

Set the key in the current PowerShell session, then explicitly select OpenAI:

```powershell
$env:OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
keywordmoves run text-library `
  --operation extract `
  --input .\lyrics.txt `
  --llm openai `
  --model gpt-4.1-mini `
  --option limit=20
```

Alternatively, supply the key as a command-line argument:

```powershell
keywordmoves run text-library --operation extract --input .\lyrics.txt --llm openai --openai-api-key "YOUR_OPENAI_API_KEY"
```

`--openai-api-key` overrides `--option llm_api_key=...` and `OPENAI_API_KEY`.
Prefer the environment variable: command-line keys may appear in shell history
or process listings. KeywordMoves does not write the key into result metadata
or include raw provider error messages in its normal CLI errors.

The default model is `gpt-4.1-mini`; use `--model` to choose another
Responses-compatible text model available to your API project. The plugin does
not silently switch providers or models. For a reasoning model, a larger token
budget may be needed; set `--option llm_max_output_tokens=2048` or higher.

See [docs/openai.md](docs/openai.md) for Bash and Python examples, supported
options, hosted-data behaviour and troubleshooting.

## Online keyword sources

Install the optional networking and HTML parsers:

```powershell
python -m pip install -e ".[online]"
keywordmoves plugins --kind keyword
```

The online suite adds twelve documented API adapters: `google-ads`,
`search-console`, `dataforseo`, `semrush`, `ahrefs`, `keywordtool`,
`keywords-everywhere`, `alsoasked`, `serpapi`, `brave-suggest`, `datamuse` and
`wikipedia`. It also includes three explicitly opt-in experimental browser
suggestion endpoints, a configurable `website-keywords` public-HTML extractor,
and **import-only** `ubersuggest` and `answerthepublic` adapters.

```powershell
keywordmoves run datamuse --operation related --keyword "independent music" --option limit=20

$env:SEMRUSH_API_KEY = "YOUR_SEMRUSH_KEY"
keywordmoves run semrush --operation related --keyword "independent music" --option database=uk
```

Online sources use `--keyword` seeds, not reference-text inputs or LLM flags.
API credentials come from provider-specific environment variables or explicit
`--option` settings. Most commercial APIs require separate API access and may
charge credits. A local output limit is not a universal billing cap.

See [the online-source guide and research matrix](docs/online-sources.md) for
all eighteen plugins, exact operations, credentials, locale conventions,
website query/extraction rules, access limitations and primary-source links.
The guide distinguishes API adapters from experimental endpoints and report
imports: no authenticated production access is implied by offline tests.

## Write a keyword plugin

Implement an object with a `descriptor` and `run(request, context)` method, then
publish it through an entry point:

```toml
[project.entry-points."keywordmoves.plugins"]
my-source = "my_package.plugin:MyKeywordPlugin"
```

The request and result dataclasses are exported from `keywordmoves`. Use
`KeywordEvidence` to state the source, metric, unit, date, geography and limits
instead of flattening unlike evidence into one unexplained score.

## Write an LLM plugin

Implement a `descriptor` and `generate(request) -> LLMResult`, then register:

```toml
[project.entry-points."keywordmoves.llms"]
my-local-runtime = "my_package.llm:MyLocalLLM"
```

Keyword plugins select it through `context.llms.get(name)`. Model-specific
options are passed with the `llm_` prefix; for example `--option
llm_device=cpu` becomes the LLM option `device=cpu`.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
```

OpenAI unit/CLI tests also run without the optional SDK. To run the additional
real-SDK transport tests (using in-memory HTTP responses, not billable API
calls), install `.[dev,openai]`. CI installs that extra and runs both test sets.

spaCy API tests use controlled annotated documents and need only the `spacy`
extra; a separate optional test uses a real `en_core_web_sm` pipeline. See
[spaCy test instructions](docs/spacy.md#tests) for both routes. CI also includes
a trained-pipeline smoke-test job.

NLTK API tests need the `nltk` extra but no downloaded resources; a separate
pretrained-model smoke test requires its English data. CI installs that data in
Linux and Windows jobs and makes a missing-resource skip a failure. See
[NLTK test instructions](docs/nltk.md#tests).

KeyBERT adapter/API tests use controlled embeddings and do not download models.
Install `.[dev,keybert]` to run both; separate Linux/Windows CI jobs require a
real pinned-model smoke test. See [KeyBERT tests](docs/keybert.md#tests).

See [docs/plugin-opportunities.md](docs/plugin-opportunities.md) for researched
next-plugin candidates and access constraints.

## Bundled skill

The repository includes
[`skills/research-keyword-database`](skills/research-keyword-database/SKILL.md),
a reusable workflow for inspecting, expanding, researching, validating and
updating the canonical My Songs keyword register. It keeps KeywordMoves
candidate generation separate from external demand evidence, requires explicit
LLM selection, and preserves the register's evidence and lifecycle semantics.

## Current limitations and integration notes

- Python 3.10–3.13 is supported. Optional NLP and provider integrations need their documented extras and source permissions.
- Source text frequency, semantic relevance, search-result samples, platform observations and provider estimates answer different questions. They are not a universal ranking score.
- Literal extraction preserves contiguous Unicode phrases and source offsets. Its English boundary stopwords are not a language-aware tokenizer; review other languages explicitly.
- Browser-observation capture paths and hashes are supplied by the caller. The importer preserves their lineage but does not certify the source capture.
- Online integrations require the access, permissions and charge limits documented in their module guides. A local result limit is not a billing ceiling.

See [literal extraction](docs/literal-text.md) and [reviewed browser observations](docs/browser-observations.md) for the new operations and reproducible examples.
