# PyPI publication

Trusted publisher configuration targets project `keywordmoves`, owner `kieransimkin`, repository `keywordmoves`, workflow `release.yml` and environment `pypi`. A pending publisher becomes an ordinary publisher on the first successful upload; configuration alone does not publish a package.

The workflow copies the already validated wheel and source archive from a matching GitHub release, verifies their checksum manifest and source/package versions, runs strict metadata checks, and uses GitHub OIDC to publish those same bytes. It never rebuilds different packages after GitHub publication and stores no PyPI credential. Versioned packages remain immutable.

Publish a matching GitHub release after the maintained tests pass, then verify the PyPI JSON API and downloaded file hashes separately. A submitted workflow or publisher configuration is not a published package.
