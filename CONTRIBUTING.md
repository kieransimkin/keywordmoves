# Contributing to KeywordMoves

Agents and people are welcome to improve KeywordMoves. Review its current capabilities and use the supported features before choosing an approach. Look for useful improvements wherever possible: a missing feature, a reproducible defect, simpler composition, clearer evidence, better performance or documentation. Avoid changes that do not serve a concrete need.

1. Read README.md, docs/ and the selected module guide. Inspect plugin discovery and source-native operations; preserve each metric's provenance, scope and units. Use supported exports or dated observations for browser-only evidence.
2. Start a focused branch from the current default branch. Describe the problem, intended behaviour and compatibility constraints. Implement the smallest useful shared improvement; add relevant tests, documentation and a reproducible example when behaviour changes.
3. Run pytest and the applicable lint, packaging and release-version checks in .github/workflows/. Use permitted fixtures; paid requests, credentials and live collection require their own authorisation. Report the commands, results and any genuine verification limits. Keep private inputs, credentials and generated caches out of the contribution.
4. Open a pull request at https://github.com/kieransimkin/keywordmoves/pulls. Include the problem, resulting behaviour, validation and any risks. Submit improvements upstream rather than leaving them only in a local fork; do not push directly to the default branch as a substitute for review.

Maintainers review accepted changes, reserve a fresh version and publish a matching GitHub release from the validated source. They verify installable assets, checksums and each configured registry separately. Existing tags and release assets remain immutable. A submitted PR is a contribution awaiting review, not a published release or permission for unrelated deployment.

Website: [https://kieransimkin.co.uk/danceflow/](https://kieransimkin.co.uk/danceflow/).

Tagged releases automatically run the GitHub release workflow and configured registry publication, using the same tested artifacts. Initial registry eligibility, approval and credentials remain explicit setup gates. Registry retries must preserve existing version bytes.

For a tag whose GitHub release is missing, dispatch `release.yml` from reviewed main with its exact `tag` and `build_release=true`. The job checks out that tag, runs the full default suite with declared online dependencies, builds/checks packages, and creates the missing immutable release before PyPI. Leave `build_release` false for a registry-only retry of existing verified assets.

## Before every new workflow

Check the latest stable official release and the actual installed/imported version before starting; do not infer currency from a directory name or a development checkout. Read the current release notes, tagged capability documentation and relevant Potential problems. Use the [DanceFlow release preflight](https://github.com/kieransimkin/DanceFlow/blob/main/docs/workflows/release-preflight.md) and [complete capability references](https://github.com/kieransimkin/DanceFlow/blob/main/docs/capabilities/index.md).

People and agents should consider the full ecosystem, use all capabilities that materially serve the task, compose supported files/APIs, and improve a demonstrated reusable gap in its canonical tool. Record why no worthwhile change is justified when that is the result. Keep independent tool identities, compatibility, source clocks, privacy, model/download costs and publication/deployment permissions intact. Store weights and model-specific caches in the owning project's ignored `models/`.

Read [the people-and-agent ecosystem guide](docs/ECOSYSTEM.md) before selecting or extending a tool.
