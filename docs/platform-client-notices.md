# Notices for an authenticated platform client

The KeywordMoves package is a local research library, not a hosted OAuth
service. Each operator must supply truthful notices for their registered
client before using integrations that require them. An OAuth console accepting
an empty policy field does not waive the platform's requirements.

The maintainer's private client has a [privacy notice](keywordmoves-local-privacy.md)
and [terms of use](keywordmoves-local-terms.md). These describe that one
configuration; they are not a substitute for another deployment's policy.
Keep account identifiers, credentials and private exports out of public notices.

Before connecting an account, review the actual data, purpose, storage,
recipients, retention/deletion procedure, contact route and scopes. Obtain
acceptance of the client's notice and the platform terms where required.
Publish the approved notice at a reachable HTTPS URL and verify its exact
content. Configure that URL in the platform's app settings. Keep the source
version and acceptance evidence privately; terms acceptance is not API approval.

For a read-only Google client, request only the scopes the selected reader uses.
For YouTube, include its Terms of Service, Google Privacy Policy and Google
security-permissions links. Track applicable refresh/deletion dates; no retention
scheduler is implied. For TikTok, keep Sandbox and production state distinct,
and use your own app's policy URLs rather than TikTok's footer links. Record
unavailable access instead of bypassing eligibility or private endpoints.

A reproducible setup review can use the [authenticated-access examples](authenticated-access.md#bounded-verification-and-evidence)
after notices, normal consent and credentials are configured. These examples
are finite first-party reads. Successful inventory/profile reads verify access,
not global keyword demand. No additional consent, account or policy details
are bundled into the package.

Primary requirements checked 8 October 2026:

- [YouTube client terms and privacy rules](https://developers.google.com/youtube/terms/developer-policies#client-terms-of-use-and-privacy-policies)
- [Google API client privacy](https://developers.google.com/terms)
- [TikTok app creation](https://developers.tiktok.com/doc/getting-started-create-an-app)
