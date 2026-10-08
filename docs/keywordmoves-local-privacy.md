# KeywordMoves Local privacy notice

Last updated: 8 October 2026.

This notice covers Kieran Simkin's private, single-user KeywordMoves Local
installation and its own registered platform clients. The public KeywordMoves
Python library is separately installable; other operators must describe their
own clients, enabled providers and data practices rather than reuse this notice
as a claim about their deployment.

## Purpose and information accessed

The local client helps its account owner inspect source-specific keyword and
performance evidence. It uses YouTube API Services when the owner enables that
integration. Google authorization requests only Search Console read access,
YouTube read access and YouTube Analytics read access. It can receive accessible
property/channel identifiers, public channel or video metadata and the owner's
selected search/performance reports. It does not request Google passwords,
monetary Analytics permissions or permission to publish or edit content.

When configured, Bing Webmaster reads can return the account's site inventory
and authorized search reports. A Bing key can cover every verified site even
though the configured readers only perform reads. TikTok Sandbox Login Kit and
Display access are limited to basic profile and public-video information under
user.info.basic and video.list. These TikTok permissions do not supply keyword
search-demand reports or grant publishing/Research API access. Native Pinterest
Trends observations and exports are imported only when the owner selects them;
that route does not obtain a Pinterest API token.

Only selected providers receive a request. They receive the authentication and
request parameters needed for that operation, such as the selected property,
channel, reporting dates or query. Google and TikTok handle their own sign-in
and consent pages. See the [Google Privacy Policy](https://policies.google.com/privacy)
and [TikTok Privacy Policy](https://www.tiktok.com/legal/page/eea/privacy-policy/en).

## Local storage, use and sharing

The configured installation stores OAuth client, access/refresh credentials and
API keys in Windows Credential Manager. The broader library also supports
explicit environment/CLI injection and opt-in macOS Keychain or Linux Secret
Service/KWallet storage; those are different deployments. Credentials and
private account exports are excluded from public GitHub source and packages.

Selected JSON/CSV reports and provenance records are saved to private local
files for the owner's evidence review. The owner and expressly authorized
reviewers can inspect those records. No hosted KeywordMoves account database,
advertising, sale of account data or automatic LLM call is configured. Optional
commercial/LLM integrations in the public library are outside this client's
configured Google/TikTok access. The local client does not add tracking cookies;
the provider's sign-in pages and document host have their own cookie practices.

No recurring collector or automatic retention job is enabled by this setup.
Saved data remain subject to their source's retention rules. The owner must
review stored YouTube API data and refresh or delete them within the applicable
30-day limit, and must remove consent-dependent data after revocation within
30 days at the latest. Files, copies and backups all count; the software does
not silently remove arbitrary files. This notice does not grant indefinite
retention or assert that saving a report makes it compliant.

## Consent, control and deletion

Read and accept this notice before authorizing the private client. Each provider
uses its normal consent/authorization flow. Cancel or revoke access whenever
needed. For Google, use [Google security permissions](https://security.google.com/settings/security/permissions)
to revoke the client's access; for TikTok, remove the connected app in the
account's app permissions. Revocation and deletion are separate actions.

Stop further reads, delete the client's stored credentials from the selected
OS store, and delete its saved reports/copies/backups when withdrawing consent.
KeywordMoves credential deletion is idempotent but does not revoke a platform
grant or clear other environment variables. The private helper's Google client
and authorized-user entries must also be removed. Local JSON/CSV exports can
be inspected and exported by the account owner.

For general privacy questions or to arrange a private follow-up about a
complaint/deletion request, contact Kieran Simkin through the project
[GitHub issues](https://github.com/kieransimkin/keywordmoves/issues). Keep
passwords, tokens, personal details and private account exports out of public
issues; an issue can request a private contact route without sharing those data. Material
changes to the private client's data access or use require an updated notice
and renewed consent before that expanded use.
