# Reviewed browser observations

Use `observed-evidence import-observations` for a permitted, dated observation
when a source has no suitable native module or export. Collection remains a
separate browser action; this plugin does not scrape, log in or bypass access
controls. Native platform modules remain preferred for supported metrics.

The `keywordmoves-observations/v1` schema requires `phrase`, `source`, `metric`,
`observed_at` and `platform` in each observation. Preserve metric units and scope.
Use an explicit unavailable state when evidence is unavailable; do not substitute
zero. Search result language and local text proposals do not establish volume.

Optional text fields `subject`, `seed_keyword`, `capture_file`, `capture_sha256`,
`evidence_kind` and `limitations` preserve catalogue identity and capture lineage
in candidate metadata. A recorded hash is a reference supplied by the caller;
the importer does not attest to the capture's accuracy or verify the file hash.

```json
{
  "schema": "keywordmoves-observations/v1",
  "observations": [{
    "phrase": "example song",
    "subject": "Example Song",
    "seed_keyword": "example song",
    "platform": "Yahoo",
    "source": "Yahoo native search",
    "source_url": "https://search.yahoo.com/search?p=example+song",
    "metric": "native-search-language",
    "value": "The reviewed result titles contain the phrase.",
    "unit": "observation",
    "observed_at": "2026-10-07",
    "geography": "GB browser scope; personalised results possible",
    "scope": "One reviewed native results page",
    "evidence_kind": "native-search-sample",
    "limitations": "No search volume, difficulty or ranking potential established."
  }]
}
```

Save the example as `observations.json`, then run:

```sh
keywordmoves run observed-evidence --operation import-observations --input observations.json --format json
```

Offline tests: `python -m pytest tests/test_observed_evidence.py`.
