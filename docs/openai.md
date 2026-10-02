# OpenAI LLM plugin

KeywordMoves' `openai` runtime implements the existing
`generate(LLMRequest) -> LLMResult` interface. Keyword plugins still select their
runtime through `context.llms.get(name)`: no OpenAI-specific import or provider
selection is added to `text-library`.

## Installation and selection

From the repository root:

```powershell
python -m pip install -e ".[openai]"
keywordmoves plugins --kind llm
```

The `openai` extra installs the official OpenAI Python SDK (`>=3,<4`). Core
installs remain dependency-free. Listing plugins and running `extract-local`
do not import the SDK, require a key, or contact OpenAI. A missing SDK is reported
only when the runtime is used with otherwise valid configuration.

This runtime uses the Responses API, not Chat Completions, Azure OpenAI or a
third-party OpenAI-compatible service. The default model is `gpt-4.1-mini`, a
non-reasoning text model suited to the existing short keyword prompts. A model
or snapshot can be selected with `--model`; it must support text generation on
the Responses API and be available to the API project. There is no hard-coded
model allowlist and no silent fallback after an error.

## API keys

Precedence, highest first:

1. `--openai-api-key KEY` (requires the selected LLM to be `openai`).
2. `--option llm_api_key=KEY` (the existing generic options mechanism).
3. The `OPENAI_API_KEY` environment variable.

A blank/invalid explicit key is an error, not permission to fall back to another
account's environment key. The environment is resolved on each `generate` call.
Direct Python callers use `LLMRequest.options["api_key"]` for an explicit key;
otherwise the environment is used. A `.env` file is **not** loaded automatically.

### PowerShell

```powershell
$env:OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
keywordmoves run text-library `
  --operation extract `
  --input .\lyrics.txt `
  --llm openai `
  --model gpt-4.1-mini `
  --option limit=20
```

### Bash / zsh

```bash
export OPENAI_API_KEY="YOUR_OPENAI_API_KEY"
keywordmoves run text-library \
  --operation extract \
  --input ./lyrics.txt \
  --llm openai \
  --model gpt-4.1-mini \
  --option limit=20
```

### Command-line key

```powershell
keywordmoves run text-library --operation extract --input .\lyrics.txt --llm openai --openai-api-key "YOUR_OPENAI_API_KEY"
```

The environment variable is preferable to putting a key in command arguments.
Command-line keys can appear in shell history and process listings. Exporting a
literal key can also put it in shell history: use your environment's secret
injection or secure prompt facilities for routine use, and never commit keys.
The plugin does not log keys, copy request options into result metadata, or echo
raw SDK/provider exceptions. Suppressed exception chaining keeps provider error
bodies out of normal CLI tracebacks. External debuggers, SDK debug logging,
process inspection and application logging are outside that guarantee.

## Options

All provider options use the existing `llm_` prefix on the keyword-plugin CLI;
the prefix is removed in `LLMRequest.options`.

| CLI setting | Default | Meaning |
| --- | --- | --- |
| `--model MODEL` | `gpt-4.1-mini` | Model or snapshot; equivalent to `--option llm_model=MODEL`. |
| `--openai-api-key KEY` | Environment fallback | Explicit key; equivalent generic option is `llm_api_key`. |
| `--option llm_max_output_tokens=2048` | `LLMRequest.max_new_tokens` (128 in text-library) | Responses output budget; includes reasoning tokens when used. Overrides the request's budget for this runtime. |
| `--option llm_max_new_tokens=512` | 128 | Existing text-library setting for the request's token budget; used when `llm_max_output_tokens` is absent. |
| `--option llm_timeout=60` | 60 seconds | Positive, finite SDK network timeout, not a deadline for the whole two-call operation. |
| `--option llm_max_retries=2` | 2 | Non-negative SDK retry count; `0` disables retries. |
| `--option llm_temperature=0.2` | Not sent | Finite value from 0 to 2; only use with models supporting this control. |
| `--option llm_reasoning_effort=low` | Not sent | Passed as `reasoning.effort`; supported values depend on the selected model. |
| `--option llm_organization=org-...` | SDK environment/default | Optional organization, otherwise the SDK's `OPENAI_ORG_ID` handling applies. |
| `--option llm_project=proj-...` | SDK environment/default | Optional project, otherwise the SDK's `OPENAI_PROJECT_ID` handling applies. |

Sampling and reasoning controls are not added automatically based on a model's
name. A non-supporting model produces a configuration error rather than a retry
with silently changed parameters. The endpoint is explicitly
`https://api.openai.com/v1`; `OPENAI_BASE_URL` is intentionally not inherited so
a setting for another application cannot redirect this runtime's credentials.
Other SDK environment settings, such as project selection and HTTP proxies,
remain applicable.

For reasoning-capable models, the text-library default of 128 output tokens can
be too small because reasoning tokens consume the same output budget. Select
your model and increase `llm_max_output_tokens`; apply `llm_reasoning_effort`
only when that model supports it. There is no automatic, potentially billable
retry with a larger budget.

## Python

```python
from keywordmoves import LLMRegistry, LLMRequest

# Reads OPENAI_API_KEY; pass options={"api_key": key, ...} for an explicit key.
llm = LLMRegistry().get("openai")
result = llm.generate(
    LLMRequest(
        task="keyword-extraction",
        prompt="Return only comma-separated search phrases about paper planes at night.",
        max_new_tokens=256,
        options={"model": "gpt-4.1-mini", "timeout": 60.0},
    )
)
print(result.text)
print(result.model)
print(result.metadata)
```

Each `generate` call creates and closes its own SDK client, including on API
errors. The registry does not retain a client containing an earlier call's key.
The returned model is the model identifier reported by the API; the originally
requested model is retained as `metadata.requested_model`.

## Hosted data, usage and output

Selecting OpenAI sends the supplied prompt text to OpenAI. It is not a local
inference mode. The current text-library `extract` operation performs **two**
model calls: extraction followed by related-concept expansion. Extraction uses
a text excerpt controlled by `max_text_chars` (currently 2400 by default), plus
local candidates; expansion sends seed phrases. API charges and provider rate
limits apply to both calls and to applicable retries.

Requests explicitly set `store=False`. This disables response storage for later
retrieval through the Responses API; it is not a guarantee of zero retention or
a replacement for OpenAI's applicable data controls. Do not submit source text
you are not authorised to send to a hosted provider. Use `extract-local` or the
Hugging Face runtime for local processing instead.

The runtime returns text, not a provider-specific JSON schema. This preserves
the existing comma-separated keyword parsing contract. Refusals, incomplete or
failed responses and empty output raise a clean error; partial text is never
returned as a successful `LLMResult`. The CLI returns exit code 2 for these
errors and does not print a partial result.

Metadata is limited to task, API name, requested model, response ID, the storage
flag and available input/output/total token counts. It excludes credentials,
request options, prompts and raw provider response/error dumps. These token
counts describe **one call**, not a price estimate. The current text-library
result exposes the extraction call's metadata under `llm_metadata`; that is not
an aggregate of both calls. LLM suggestions remain proposals, not evidence of
search demand, popularity or ranking potential.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing optional SDK | From this checkout, run `python -m pip install -e ".[openai]"`. |
| Missing key | Set `OPENAI_API_KEY` in the process environment or supply `--openai-api-key` with `--llm openai`. |
| Authentication failed | Check the key; an explicit key takes precedence over the environment. |
| Access denied/model unavailable | Check the selected model and the API key's project permissions. |
| Request rejected | Check model support for Responses, input length and the configured sampling/reasoning controls. |
| Rate limit/quota | Check API billing and usage limits, reduce request frequency, then retry as appropriate. |
| Timeout/connection failure | Check networking/TLS/proxies; adjust `llm_timeout` and `llm_max_retries` deliberately. |
| Output-token limit | Increase `llm_max_output_tokens`, particularly for reasoning models. Partial suggestions are discarded. |
| Refusal or other incomplete response | Review the supplied task/input; no suggestions from that response are used. |

## Tests and sources

```powershell
python -m pip install -e ".[dev,openai]"
python -m pytest
python -m ruff check .
```

The unit and CLI tests use a fake SDK and work without OpenAI dependencies.
`tests/test_openai_sdk.py` also exercises serialization, parsing and error
mapping through the real SDK and an in-memory HTTPX2 transport. It is skipped
without the optional SDK; CI installs the extra. Neither suite contacts OpenAI
or requires a real API key. A live smoke test is a separate, potentially billable
run using the examples above.

Official references checked for this implementation (2 October 2026):

- [OpenAI Python SDK](https://developers.openai.com/api/reference/python)
- [Text generation / Responses](https://developers.openai.com/api/docs/guides/text)
- [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [Data controls](https://developers.openai.com/api/docs/guides/your-data)
