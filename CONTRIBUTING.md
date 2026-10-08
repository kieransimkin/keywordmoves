# Contributing to KeywordMoves

Agents and people are welcome to improve KeywordMoves. Review its current capabilities and use the supported features before choosing an approach. Look for useful improvements wherever possible: a missing feature, a reproducible defect, simpler composition, clearer evidence, better performance or documentation. Avoid changes that do not serve a concrete need.

1. Read README.md, docs/ and the selected module guide. Inspect plugin discovery and source-native operations; preserve each metric's provenance, scope and units. Use supported exports or dated observations for browser-only evidence.
2. Start a focused branch from the current default branch. Describe the problem, intended behaviour and compatibility constraints. Implement the smallest useful shared improvement; add relevant tests, documentation and a reproducible example when behaviour changes.
3. Run pytest and the applicable lint, packaging and release-version checks in .github/workflows/. Use permitted fixtures; paid requests, credentials and live collection require their own authorisation. Report the commands, results and any genuine verification limits. Keep private inputs, credentials and generated caches out of the contribution.
4. Open a pull request at https://github.com/kieransimkin/keywordmoves/pulls. Include the problem, resulting behaviour, validation and any risks. Submit improvements upstream rather than leaving them only in a local fork; do not push directly to the default branch as a substitute for review.

Maintainers review accepted changes, reserve a fresh version and publish a matching GitHub release from the validated source. They verify installable assets, checksums and each configured registry separately. Existing tags and release assets remain immutable. A submitted PR is a contribution awaiting review, not a published release or permission for unrelated deployment.

Website: [https://kieransimkin.co.uk/danceflow/](https://kieransimkin.co.uk/danceflow/).

Tagged releases automatically run the GitHub release workflow and configured registry publication, using the same tested artifacts. Initial registry eligibility, approval and credentials remain explicit setup gates. Registry retries must preserve existing version bytes.
