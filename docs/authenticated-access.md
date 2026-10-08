# Authenticated platform access

KeywordMoves accepts legitimately obtained credentials through command-line
options, environment variables and optional cross-platform OS keyring storage.
OAuth client creation, consent, token renewal and platform approval remain
external setup steps. Install the `online` extra for live readers and the
`credentials` extra for OS storage. Nothing here starts a collector or scheduled
job. See [credential configuration](credentials.md) for precedence, profiles
and examples.

Keep three independently verified states:

- **Browser:** the native site identifies the signed-in account and exposes the
  requested report or export.
- **Connector:** the connector lists the authorized account/channel and a
  permitted operation succeeds. Its tokens are not available for extraction.
- **Local API:** the local client has the required approved scopes/entitlements
  and a bounded first-party request succeeds. Browser/connector success does
  not prove this state.

## First-party credentials and scope

| Reader | Local environment | Minimum applicable access |
| --- | --- | --- |
| Google Search Console | `SEARCH_CONSOLE_ACCESS_TOKEN` | OAuth `https://www.googleapis.com/auth/webmasters.readonly`; access to the exact property. |
| YouTube Data API with OAuth | `YOUTUBE_ACCESS_TOKEN`, `auth=oauth` | OAuth `https://www.googleapis.com/auth/youtube.readonly`; owner access for `mine=true`. Captions have separate permissions. |
| YouTube Analytics | `YOUTUBE_ACCESS_TOKEN`, `YOUTUBE_CHANNEL_ID` | Both `https://www.googleapis.com/auth/youtube.readonly` and `https://www.googleapis.com/auth/yt-analytics.readonly`; channel-owner authorization. No monetary scope is needed. |
| Bing Webmaster | `BING_WEBMASTER_API_KEY`, or documented OAuth alternative | Webmaster authorization. A user API key covers all verified sites and is not a read-only/property-limited credential. KeywordMoves implements read operations. |
| TikTok Display API | `TIKTOK_ACCESS_TOKEN` | Approved Login Kit/Display products and granted `user.info.basic` / `video.list`; optional profile fields require their actual additional scopes. Sandbox access differs from production approval. |

Google private reports need OAuth, not an API key. Use a separate appropriately
configured installed-app client and Google's supported consent/loopback flow.
Do not reuse unrelated keys or weaken browser access controls. Honor token
expiry, testing-mode limitations and revocation; a refresh token does not
guarantee permanent access. Request only scopes used by the reader.

TikTok Display data cover the authorizing account's profile/public videos;
they do not supply search-demand, watch-time or keyword-referral reports.
Research API eligibility and approval are separate requirements. Read
[TikTok](tiktok.md) before use.

For Pinterest and other browser/export surfaces, use the native report and
[reviewed import routes](native-export.md). Native Trends access does not
establish a Trends API entitlement. Keep the actual region/window and indexed,
approximate or unavailable values; do not invent absolute volume.

## Cross-platform secret storage

Use the built-in optional store, which delegates to supported OS backends:
Windows Credential Manager, macOS Keychain, or Linux Secret Service/KWallet.
It never silently falls back to plaintext files. On a headless host without an
available OS store, environment or explicit CLI credentials remain supported.
See [credentials](credentials.md) for setup, limits and commands.

Returned inventories are private account evidence. Keep them outside public
repositories/distributions. Never print tokens, client secrets, refresh tokens
or signed links in logs, reports, examples or support requests. OAuth renewal
remains the responsibility of a supported external library; store an already
authorized token and refresh it when necessary.

## Bounded verification and evidence

With the approved credential already injected, inventory reads can use:

```powershell
keywordmoves run google-search --operation gsc-sites --option max_requests=1
keywordmoves run bing-search --operation bwt-sites --option max_requests=1
keywordmoves run tiktok --operation display-user --option max_requests=1
```

Run only the authorized platform; this is not a batch command. Inventory/profile
success establishes access, not demand. A separate YouTube Analytics read needs
the intended channel and settled dates:

```powershell
keywordmoves run youtube --operation analytics-search `
  --option start_date=2026-09-01 --option end_date=2026-09-30 `
  --option report_limit=25 --option max_requests=1
```

Preserve unmodified private output, request count, account/property/channel,
scope, capture date and returned reporting window. An empty/thresholded report
is not zero global demand. Native attribution covers that account and surface.
On failure retain the non-secret error; do not retry, rotate a key or switch
providers silently. Paid sources, new grants, platform terms and production
app submissions need their applicable separate authorization.

## Primary references

Checked 8 October 2026:

- [Google installed-app OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Search Console authorization](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing)
- [YouTube Analytics reports.query scopes](https://developers.google.com/youtube/analytics/reference/reports/query)
- [Bing Webmaster API access](https://learn.microsoft.com/en-us/bingwebmaster/getting-access)
- [TikTok Display API](https://developers.tiktok.com/docs/en/display-api-overview)
- [Pinterest Trends](https://help.pinterest.com/en/business/article/pinterest-trends)
- [Keyring documentation](https://keyring.readthedocs.io/en/latest/)
