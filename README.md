# KeywordMoves

KeywordMoves is the keyword-discovery and search-evidence layer in Kieran
Simkin's DanceFlow ecosystem. It provides small, composable Python plugins for
generating keywords, finding related language, and attaching clearly labelled
evidence such as relative Google Trends interest.

It keeps two plugin systems deliberately separate:

- `keywordmoves.plugins` contains keyword sources and analysers.
- `keywordmoves.llms` contains interchangeable LLM runtimes.

A keyword plugin that needs language-model inference must ask for an LLM plugin
by name. It must not import or silently select a model provider of its own.

## Current plugins

| Plugin | Kind | What it does |
| --- | --- | --- |
| `google-trends` | keyword | Imports Google Trends interest and related-query CSV exports, preserving the distinction between relative index values and absolute search volume. |
| `observed-evidence` | keyword | Validates and imports dated browser, account and public-tool observations without scraping authenticated or private interfaces. |
| `spacy` | keyword | Extracts proper nouns, noun chunks, named entities and useful noun/adjective phrases from reference text with a local spaCy pipeline. No LLM required. |
| `text-library` | keyword | Reads `.txt`, `.md`, `.lrc`, or `.csv` files, produces transparent local candidates, then asks a selected LLM plugin for semantic keywords and related concepts. |
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

See [docs/plugin-opportunities.md](docs/plugin-opportunities.md) for researched
next-plugin candidates and access constraints.

## Bundled skill

The repository includes
[`skills/research-keyword-database`](skills/research-keyword-database/SKILL.md),
a reusable workflow for inspecting, expanding, researching, validating and
updating the canonical My Songs keyword register. It keeps KeywordMoves
candidate generation separate from external demand evidence, requires explicit
LLM selection, and preserves the register's evidence and lifecycle semantics.

## Potential problems

### Pytest cannot create `tmp_path` under the managed user temp directory

- **Symptom:** a test using `tmp_path` fails before its body runs with
  `PermissionError: [WinError 5]` below
  `C:\Users\Kieran\AppData\Local\Temp\pytest-of-Kieran`.
- **Cause when observed:** the managed process could not create pytest's
  default per-user temporary hierarchy even though the repository was
  writable.
- **Action that worked:** prefer immutable checked-in fixtures when the test
  does not genuinely require a temporary write. When temporary files are part
  of the behaviour under test, use a dedicated repository-local
  `--basetemp` path and remember that pytest clears that exact directory at the
  start of a run.
- **Verification:** the observed-evidence validation cases run entirely from
  checked-in fixtures and the complete suite passes without requesting the
  inaccessible user-temp directory.
- **Source checked:** pytest temporary-directory documentation, accessed 1
  October 2026: <https://docs.pytest.org/en/stable/how-to/tmp_path.html>.

### The Hugging Face model is not installed or cannot be downloaded

- **Symptom:** the text-library run reports missing optional dependencies, a
  missing local model, or a model-download/network error.
- **Cause:** model inference is intentionally optional and lazy. Core installs
  do not include PyTorch or Transformers, and first use normally needs network
  access to Hugging Face.
- **Action:** install `keywordmoves[huggingface]`. For offline work, prefetch the
  model into an approved local cache and pass `llm_cache_dir=<path>` plus
  `llm_local_files_only=true`.
- **Verification:** list the LLM plugin, then run a short text fixture and check
  that the result metadata names both the selected plugin and model.

### Google Trends values are mistaken for search volume

- **Symptom:** an imported `0-100` value is described as a number of searches.
- **Cause:** Google Trends exports normalised relative interest rather than
  absolute search-volume counts.
- **Action:** retain the emitted metric name, unit and note. Use a separate
  source such as Google Ads Keyword Planner for volume estimates.
- **Verification:** JSON output must label the unit as `index_0_100` and include
  the relative-interest caveat.

### A keyword plugin silently chooses its own LLM

- **Symptom:** an LLM-backed operation runs without `--llm`, or changing the LLM
  requires editing the keyword plugin.
- **Cause:** the keyword plugin has crossed the two plugin boundaries.
- **Action:** resolve the requested provider only through `context.llms`; require
  an explicit selection for LLM-backed operations and keep a separate non-LLM
  operation where useful.
- **Verification:** the `text-library` tests require `--llm` for `extract` and
  prove that a registered fake LLM can replace the built-in runtime.

### Package builds fail with `WinError 5` in a temporary directory

- **Symptom:** `python -m build` fails while creating `build-env-*\\Include` or
  writing a `pyproject_hooks` `input.json`, with `PermissionError: [WinError 5]
  Access is denied`.
- **Cause in the managed Windows workspace:** Python build hooks could not write
  their normal user-temp files. PyPA Build creates isolated environments with
  `tempfile.mkdtemp`; moving `TEMP` alone did not remove the managed-process
  restriction.
- **Action that worked:** keep build inputs in the repository, set `TEMP` and
  `TMP` to the ignored `.tmp` directory, and run the standard build with the
  narrowly approved filesystem access needed for its hook subprocesses. Use
  `--no-isolation` only when the declared build backend is already installed.
- **Verification:** both `keywordmoves-0.1.0-py3-none-any.whl` and
  `keywordmoves-0.1.0.tar.gz` were produced from the same source tree.

### Setuptools rejects a redundant licence classifier

- **Symptom:** package metadata generation raises `InvalidConfigError` and says
  licence classifiers have been superseded by licence expressions.
- **Cause:** `pyproject.toml` contained both the SPDX expression `license =
  "MIT"` and the legacy MIT Trove classifier.
- **Action:** retain the SPDX expression and remove the redundant classifier.
- **Verification:** current Setuptools completed wheel and source-distribution
  metadata generation.

### Pytest passes but cannot create its cache in the managed workspace

- **Symptom:** all tests pass, followed by `PytestCacheWarning: could not create
  cache path` and `PermissionError: [WinError 5]` for a temporary
  `pytest-cache-files-*` directory.
- **Cause in the managed Windows workspace:** pytest's cache provider creates a
  temporary directory beside `.pytest_cache` before atomically renaming it, and
  that cache-only write was denied. The tests themselves had completed.
- **Action that worked:** when cross-session `--lf`/`--ff` state is not needed,
  run `python -m pytest -p no:cacheprovider`. Do not hide unrelated warnings.
- **Verification:** all 17 tests passed again without the cache warning.
- **Source checked:** pytest's maintained `cacheprovider.py`, accessed 1 October
  2026: <https://github.com/pytest-dev/pytest/blob/main/src/_pytest/cacheprovider.py>.

### `python -m build` resolves the ignored output folder instead of PyPA Build

- **Symptom:** Python reports `No module named build.__main__; 'build' is a
  package and cannot be directly executed` while the repository contains an
  ignored `build/` directory.
- **Cause when verified:** the selected project environment did not contain the
  PyPA Build frontend, so Python resolved the local `build/` directory as a
  namespace package. Its `build.__file__` was `None` and its only path was the
  repository output folder.
- **Action:** install the declared `dev` extra, which now includes PyPA Build,
  before invoking `python -m build`. For an already provisioned external build
  interpreter, run from the parent directory and pass the repository as the
  explicit source argument so the output folder cannot shadow the frontend.
- **Verification:** PyPA Build 1.6.1 produced the source distribution and copied
  the bundled skill, schema reference and UI metadata into it.
- **Source checked:** PyPA Build's CLI documentation, accessed 1 October 2026:
  <https://build.pypa.io/en/latest/reference/cli.html>.

### The `tar` command resolves to the old WinAVR utility

- **Symptom:** `Get-Command tar` points at
  `C:\WinAVR-20100110\utils\bin\tar.exe`, and archive inspection fails instead
  of listing the source distribution.
- **Action:** do not use that executable for release QA. Inspect `.tar.gz`
  source distributions read-only with Python's standard `tarfile` module, and
  use PowerShell `Expand-Archive` for ZIP files.
- **Verification:** `tarfile` confirmed all three
  `skills/research-keyword-database` files in the built source distribution.

### PowerShell parses punctuation inside a complex inline Python check

- **Symptom:** an inline `python -c` archive check containing a quoted version
  requirement with a comma and `<` fails in Windows PowerShell before Python
  starts, with parser errors such as `The '<' operator is reserved for future
  use`.
- **Cause:** the nested native-command quoting did not preserve the intended
  Python argument through Windows PowerShell 5.1.
- **Action:** avoid a densely nested inline assertion. Pass complex code through
  a reviewed script or safely encoded argument; for the current QA, narrow the
  read-only assertion to the required archive member names.
- **Verification:** the corrected `tarfile` check ran and confirmed the skill,
  schema and UI metadata in the final source archive.
- **Source checked:** Microsoft PowerShell `about_Parsing`, accessed 1 October
  2026: <https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_parsing>.
