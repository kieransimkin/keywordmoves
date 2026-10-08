# Credential configuration

KeywordMoves supports three routes across its authenticated online readers,
provider adapters, OpenAI runtime and first-party monitor collectors:

1. An explicit credential option, such as `--option access_token=...`,
   `--option api_key=...`, provider `login`/`password` options, or
   `--openai-api-key` with `--llm openai`.
2. The reader's documented environment variable, such as
   `SEARCH_CONSOLE_ACCESS_TOKEN` or `BING_WEBMASTER_API_KEY`.
3. An explicitly selected OS keyring, using that same environment-variable
   name as the credential name.

Credential options remain opaque text, including numeric or boolean-looking
values; ordinary options keep their documented types. The first supplied route wins. An invalid or blank explicit option/environment
value raises an error rather than silently selecting another account. Existing
CLI and environment use requires no keyring installation. Neither presence nor
successful storage proves scopes, API entitlement, token validity or demand.
Credential-management operations make zero network requests.

## Install and select an OS store

```sh
python -m pip install "keywordmoves[online,credentials]"
keywordmoves credentials set SEARCH_CONSOLE_ACCESS_TOKEN
keywordmoves credentials status SEARCH_CONSOLE_ACCESS_TOKEN
keywordmoves --credential-store os-keyring run google-search --operation gsc-sites --option max_requests=1
keywordmoves credentials delete SEARCH_CONSOLE_ACCESS_TOKEN
```

`set` uses hidden terminal input and confirms readback without printing the
value. For automation, `credentials set NAME --stdin` reads a bounded single
line from a secure pipe; do not put a literal secret in the pipeline command or
shell history. There is no CLI command to print stored secrets. `delete` is
idempotent and affects the selected store only; it does not revoke a token at
the platform or clear an environment variable.

Credential-management commands default to `os-keyring`. Reader commands
continue to default to `environment`, so they do not access your OS keyring
until you select it. Global flags go **before** `run`, `monitor` or `credentials`.

| Platform | Supported backend | Requirements and limits |
| --- | --- | --- |
| Windows | `keyring.backends.Windows.WinVaultKeyring` | User's Windows Credential Manager; OS permissions and blob-size limits apply. |
| macOS | `keyring.backends.macOS.Keyring` | User's Keychain; macOS/Python compatibility and access prompts follow Keyring's current requirements. |
| Linux desktop | `keyring.backends.SecretService.Keyring` | Available D-Bus session, Secret Service and unlocked collection; install required OS components. |
| KDE Linux | `keyring.backends.kwallet.DBusKeyring` / `DBusKeyringKWallet4` | Available D-Bus, KWallet and its system Python D-Bus dependency. |
| Headless Linux / containers / CI | Environment or explicit CLI; OS store only if configured | No assumption of an unlocked desktop store; secret-manager injection remains supported. |

KeywordMoves uses Keyring's configured backend. If it is a Chainer, it selects
one supported OS backend directly in priority order; it does not delegate to
plaintext, null or unsupported fallback backends. If storage is locked,
unavailable, unsupported or over the OS size limit, the operation fails with a
message that omits backend exception details and values. No plaintext fallback
file is created. OS storage protects data at rest according to that OS; it is
not isolation from other processes running as the same user.

## Account profiles and environment configuration

Use a non-secret service name to separate accounts:

```sh
keywordmoves --credential-service example-account credentials set YOUTUBE_ACCESS_TOKEN
keywordmoves --credential-store os-keyring --credential-service example-account run youtube --operation analytics-search --option channel_id=UCexample --option start_date=2026-09-01 --option end_date=2026-09-30 --option max_requests=1
```

The default service is `KeywordMoves`. `--credential-service` selects a
namespace, not an authenticated account. Validate the returned identity before
relying on it. Setting a different profile does not override a credential
already supplied in the environment or explicit options.

For Bash/Zsh, non-secret store settings can be configured as:

```sh
export KEYWORDMOVES_CREDENTIAL_STORE=os-keyring
export KEYWORDMOVES_CREDENTIAL_SERVICE=example-account
```

PowerShell uses:

```powershell
$env:KEYWORDMOVES_CREDENTIAL_STORE = 'os-keyring'
$env:KEYWORDMOVES_CREDENTIAL_SERVICE = 'example-account'
```

Explicit global flags override those settings. Use `--credential-store
environment` to disable OS fallback. `credentials status` prints only
presence booleans, the backend, service and a zero-network-request note; with
`--credential-store environment` it does not import/access Keyring at all.

## Existing environment and CLI routes

Tokens may still be injected from any approved secret manager into the reader's
documented variable. Interactive examples keep the value out of command text:

```sh
read -r -s -p "Authorized token: " SEARCH_CONSOLE_ACCESS_TOKEN
export SEARCH_CONSOLE_ACCESS_TOKEN
keywordmoves run google-search --operation gsc-sites --option max_requests=1
unset SEARCH_CONSOLE_ACCESS_TOKEN
```

```powershell
$credential = Get-Credential -UserName 'token' -Message 'Authorized token'
$env:SEARCH_CONSOLE_ACCESS_TOKEN = $credential.GetNetworkCredential().Password
keywordmoves run google-search --operation gsc-sites --option max_requests=1
Remove-Item Env:SEARCH_CONSOLE_ACCESS_TOKEN
```

The explicit route remains available when suitable for your execution context:

```sh
keywordmoves run google-search --operation gsc-sites --option access_token=YOUR_AUTHORIZED_TOKEN --option max_requests=1
```

`YOUR_AUTHORIZED_TOKEN` is a placeholder, not a working credential. Literal
arguments can be visible in shell history/process listings; OS storage or
secret-manager environment injection is generally preferable. Do not commit
credentials, logs containing them, account exports or OAuth client files.
Non-secret configuration (channel/property IDs, Graph version, user-agent,
request bounds) remains in documented options/environment variables and is
not silently treated as a secret.

## Library composition and token renewal

```python
from keywordmoves.credentials import OSCredentialStore, credential_context
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry

# Obtain/refresh authorization through the platform's supported external SDK.
# Store it using OSCredentialStore("example-account").set(NAME, token).
with credential_context(store="os-keyring", service="example-account"):
    result = PluginRegistry().get("google-search").run(
        PluginRequest("gsc-sites", options={"max_requests": 1}),
        ExecutionContext(llms=None),
    )
```

Selection uses context-local settings and restores them after nested calls or
errors. Separate asynchronous contexts can select different profiles. New
threads/processes must select their own context or use documented environment
configuration. This layer stores opaque credentials, not OAuth clients or
refresh logic, and does not create platform accounts or obtain new grants.
See [authenticated access](authenticated-access.md) and each module guide for
scopes, platform approval, token expiry and bounded evidence checks.

## Verification

Synthetic tests exercise all supported backend identities, precedence,
namespace isolation, asynchronous context separation, unavailable/locked stores,
value-free errors, CLI management and HTTPX reader requests without live
accounts. The reproducible [offline example](../examples/credential_precedence.py)
uses fictional credentials only and no network requests. Native Windows
storage is separately verified during release installation; mocked tests do
not establish live macOS/Linux desktop integration.

Primary reference checked 8 October 2026:
[Keyring documentation](https://keyring.readthedocs.io/en/latest/).
