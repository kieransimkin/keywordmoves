# PyPI publication

The configured pending publisher targets project `keywordmoves`, owner `kieransimkin`, repository `keywordmoves`, workflow `release.yml` and environment `pypi`. It becomes an ordinary publisher on the first successful upload; registration alone does not reserve the package name or publish a package.

The workflow copies the already validated wheel and source archive from a matching GitHub release, verifies their checksum manifest and source/package versions, runs strict metadata checks, and uses GitHub OIDC to publish those same bytes. It never rebuilds different packages after GitHub publication and stores no PyPI credential. Versioned packages remain immutable.

Merge this workflow with the active release owner's successor source and publish a new matching GitHub release after the maintained tests pass. Then verify the PyPI JSON API and downloaded file hashes separately. An unsent or unmerged candidate is not a published tool improvement.
