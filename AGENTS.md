# Agent instructions for KeywordMoves

- Read README.md, docs/ and the selected module guide. Inspect plugin discovery and source-native operations; preserve each metric's provenance, scope and units. Use supported exports or dated observations for browser-only evidence.
- Use the relevant existing capabilities as fully as the task warrants. Always look for a useful improvement to this tool. Fix a reusable gap here rather than copying its engine or carrying a private workaround; record why no change is justified when that is the result.
- Submit improvements as PRs to https://github.com/kieransimkin/keywordmoves/pulls. Never leave a completed reusable improvement only in a local fork. Keep changes focused and preserve the tool's identity and compatibility contracts.
- Add relevant regression coverage, documentation and a reproducible example when behaviour changes. Run pytest and the applicable lint, packaging and release-version checks in .github/workflows/. Use permitted fixtures; paid requests, credentials and live collection require their own authorisation.
- Keep credentials, account data, private media and rights records out of source, fixtures, logs and packages. Use synthetic fixtures or explicitly authorised public examples and preserve their provenance/licences.
- Use [CONTRIBUTING.md](CONTRIBUTING.md) for review. Maintainers publish accepted, validated improvements as a fresh matching GitHub release and verify configured registry destinations. Preserve existing tags and release assets; publication and deployment remain subject to applicable authorisation.
- Website: https://kieransimkin.co.uk/danceflow/

## Keep README documentation current

- Whenever a feature is added or changed, update the relevant README.md in the same change to reflect the resulting behaviour. This applies to every feature addition or modification, including APIs, commands, configuration, defaults, integrations and removals.
- Update affected feature descriptions, usage instructions, options, examples and limitations. For tools or packages with their own README.md, update that README as well as the repository README when its overview or usage is affected.
- Check the README against the implemented and verified behaviour before committing, opening or merging a PR, publishing a release or handing off the work. A feature change is incomplete until its README documentation is current; a changelog or other documentation does not replace this requirement.

## Full ecosystem and release preflight

Before every new workflow run, check the latest stable official release and observe the actual runtime used by the job. Follow [the shared preflight](https://github.com/kieransimkin/DanceFlow/blob/main/docs/workflows/release-preflight.md) and this project's [people-and-agent guide](docs/ECOSYSTEM.md). Review the complete relevant capability inventory rather than only a familiar command. Consider every DanceFlow component against the actual task; use and compose all applicable supported capabilities, and improve a concrete reusable gap in its canonical repository. Record why existing capabilities suffice when no change is justified. No catalogue checklist expands the task, authorises model downloads/training, or proves integration. Preserve exact source/package identity, clocks, model-local storage, privacy, costs, immutable releases and separate deployment gates.

## Preflight temporary-directory access

Before any long, costly, model-backed, media-rendering, build, test, packaging or release command that creates private or atomic temporary directories, run a disposable preflight with the exact runtime, user or sandbox, and intended output parent. The probe must create a unique directory, write and read a file, atomically rename it, then remove the file and directory.

Keep temporary data and caches under the owning project on `Z:`; model weights and model-specific caches remain under that project's ignored `models/`. If the probe returns `WinError 5`, access denied, a rename failure or a cleanup failure, stop before the expensive command. Preserve the exact error and use the documented narrowly approved unrestricted recovery from the outset only when the task requires it. Use a fresh output directory and do not repeat the already-failing full job in the restricted shell.

After any unrestricted recovery, verify that the final output is readable and writable from the normal managed environment. If it is not, copy the completed bytes into a pre-created project-local directory that passed the probe, verify hashes, and retain the original failure and recovery evidence. Do not edit global ACLs, switch caches to `C:`, terminate unrelated processes or claim the underlying permission problem is fixed. Repositories that own temporary-directory creation should provide and test a reusable preflight helper rather than relying only on this instruction.